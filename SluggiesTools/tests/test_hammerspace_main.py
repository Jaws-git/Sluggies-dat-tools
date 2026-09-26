import base64
import json
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

if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import synthetic_donor


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

    @staticmethod
    def _rebuilt_skinning(sk1s, sk2s):
        return main.SkinningData(
            skn_offset=0, gpl_base_offset=0, mem_clr_ptr_field_offset=0,
            mem_clr_sze_field_offset=0, mem_clr_ptr_value=0, mem_clr_absolute_ptr=0,
            mem_clr_size=0, flush_ind_arr_field_offset=0, flush_ind_absolute_ptr=0,
            flush_ind_size=1, flush_ind_data=b'\x00\x00', quantize_info=9,
            sk1s=sk1s, sk2s=sk2s, sk_accs=[],
        )

    @staticmethod
    def _sk1(gva, vertex_offset, count, fill):
        return main.SK1(
            bone_index=0, vertex_cnt=count, vertex_offset=vertex_offset,
            bind_pose_data=bytes([fill]) * (count * 12), vertex_arr_field_offset=0,
            gpl_vertex_arr_field_offset=0, vertex_arr_absolute_ptr=0,
            gpl_vertex_arr_value=gva,
        )

    def test_rebuilt_source_arrays_mirror_position_buffer_including_gap_lines(self):
        # Donor rule (361/361 models): src = align32(struct end) + gplVertexArr.
        # SK1[1] sits two cache lines after SK1[0]'s last line (an unused
        # position-buffer line at 0x20..0x40); SK2 lands at 0x60 + 8.
        sk2 = main.SK2(
            bone_index1=0, bone_index2=1, vertex_cnt=1, vertex_offset=8,
            bind_pose_data=b'\x03' * 12, weight_data=b'\x80\x80',
            vertex_arr_field_offset=0, weight_arr_field_offset=0,
            gpl_vertex_arr_field_offset=0, vertex_arr_absolute_ptr=0,
            weight_arr_absolute_ptr=0, gpl_vertex_arr_value=0x60,
        )
        skinning = self._rebuilt_skinning(
            [self._sk1(0x00, 0, 2, 0x01), self._sk1(0x40, 4, 1, 0x02)], [sk2])

        block = main.BuildSKNSkinningData(SimpleNamespace(skinning=skinning), None)

        base = 0x24 + 2 * 0x40 + 0x74       # struct end 0x118
        base = (base + 31) & ~31             # 0x120
        sk1_offset = struct.unpack_from('>I', block, 0x08)[0]
        sk2_offset = struct.unpack_from('>I', block, 0x0C)[0]
        sources = [
            struct.unpack_from('>I', block, sk1_offset + 0x30)[0],
            struct.unpack_from('>I', block, sk1_offset + 0x40 + 0x30)[0],
            struct.unpack_from('>I', block, sk2_offset + 0x60)[0],
        ]
        self.assertEqual(sources, [base + 0x00, base + 0x40, base + 0x60])
        self.assertEqual(block[base + 0x40 + 4:base + 0x40 + 16], b'\x02' * 12)
        self.assertEqual(block[base + 0x60 + 8:base + 0x60 + 20], b'\x03' * 12)
        # tail arrays start right after the mirrored region (0x60 + 20 -> 0x80)
        self.assertEqual(struct.unpack_from('>I', block, 0x1C)[0], base + 0x80)

    def test_non_exclusive_destinations_fall_back_to_sequential_sources_with_warning(self):
        skinning = self._rebuilt_skinning(
            [self._sk1(0x00, 0, 1, 0x01), self._sk1(0x0C, 0, 1, 0x02)], [])

        with mock.patch.object(main._slogger, 'warning') as warning:
            block = main.BuildSKNSkinningData(SimpleNamespace(skinning=skinning), None)

        sk1_offset = struct.unpack_from('>I', block, 0x08)[0]
        second_source = struct.unpack_from('>I', block, sk1_offset + 0x40 + 0x30)[0]
        self.assertEqual(second_source, 0xC0 + 0x20)
        self.assertIn('not cache-line exclusive', warning.call_args[0][0])


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

    def test_clone_gpl_reads_hammerspace_source_from_output_dat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'input.dat'
            output_dat = pathlib.Path(temp_dir) / 'output.dat'
            model_offset = 0x40
            input_dat.write_bytes(b'\x00' * 0x80)
            output = bytearray(0x80)
            struct.pack_into('>5I', output, model_offset, 0, 0x20, 0x24, 0, 0)
            output[model_offset + 0x20:model_offset + 0x24] = b'GPL!'
            output_dat.write_bytes(output)

            with (
                mock.patch.object(main.hh, 'BASE_SIZE', model_offset),
                mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)),
                mock.patch.object(main.hh, 'OUTPUT_DAT', str(output_dat)),
            ):
                cloned = main.CloneGPL(model_offset, 0x40)

        self.assertEqual(cloned, b'GPL!')

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

        # F10: every section start is padded up to the next 32-byte boundary.
        self.assertEqual(struct.unpack_from('>5I', result.block), (0, 32, 64, 96, 128))
        self.assertEqual(result.block[0x20:0x23], b'GPL')
        self.assertEqual(result.block[0x23:64], b'\x00' * (64 - 0x23))
        self.assertEqual(result.block[64:67], b'ACT')
        self.assertEqual(result.block[67:96], b'\x00' * (96 - 67))
        self.assertEqual(result.block[96:99], b'TEX')
        self.assertEqual(result.block[99:128], b'\x00' * (128 - 99))
        self.assertEqual(result.block[128:131], b'SKN')
        self.assertEqual(result.block[131:160], b'\x00' * (160 - 131))
        self.assertEqual(result.block[160:], b'TAIL')
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

    def test_desired_texture_assignments_require_gpl_tex_build_and_reimport(self):
        self.data['SluggiesModel']['DesiredTextureAssignments'] = {'sm0_ds1': 1}

        with self.assertRaisesRegex(
            ValueError,
            'require GPL and TEX build modes with ReimportTextures enabled',
        ):
            main.BuildModelBlock(self.data)

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

    def _build_archive_entry(self, temp_dir, entry_offset=0x1000, entry_length=0x100):
        """Write a 3-slot archive DOL entry and return its path.

        Slot 0 at +0x20 and slot 2 at +0xA0 carry recognisable filler; slot 1 at
        +0x60 is the model the builder replaces.
        """
        input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
        entry = bytearray(entry_length)
        struct.pack_into('>4I', entry, 0, 3, 0x20, 0x60, 0xA0)
        entry[0x20:0x60] = b'A' * 0x40
        entry[0x60:0xA0] = b'B' * 0x40
        entry[0xA0:0x100] = b'C' * 0x60
        input_bytes = bytearray(entry_offset + entry_length)
        input_bytes[entry_offset:entry_offset + entry_length] = entry
        input_dat.write_bytes(input_bytes)
        return input_dat

    def _build_archive_block(self, model_offset=0x1060, model_length=0x40):
        self.data['SluggiesModel'].update({
            'ModelOffset': model_offset,
            'ModelLength': model_length,
        })
        patches = self._patch_common()
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._build_archive_entry(temp_dir)
            with (
                mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)),
                mock.patch.object(main.hh, 'readDolEntry', return_value=(0x1000, 0x100)),
                patches[1], patches[2], patches[3], patches[4], patches[5],
                patches[6], patches[7], patches[8],
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {}},
                }),
            ):
                return main.BuildModelBlock(self.data)

    def test_build_rebuilds_an_archive_entry_around_the_replaced_member(self):
        result = self._build_archive_block()
        report = result.validation_report

        self.assertEqual(report['container_prefix_size'], 0x60)
        self.assertEqual(report['archive_file_count'], 3)
        self.assertEqual(report['archive_member_slot'], 1)
        self.assertEqual(report['archive_member_original_size'], 0x40)
        self.assertEqual(report['archive_slots_shifted'], 1)
        self.assertEqual(report['archive_new_size'], len(result.block))
        self.assertEqual(report['assembled_size'], len(result.block))
        self.assertEqual(report['original_size'], 0x100)

    def test_build_carries_the_other_archive_members_along_unchanged(self):
        result = self._build_archive_block()
        delta = result.validation_report['archive_size_delta']

        self.assertGreater(delta, 0)
        # Slot 0 sits before the edit, slot 2 after it.
        self.assertEqual(result.block[0x20:0x60], b'A' * 0x40)
        self.assertEqual(result.block[0xA0 + delta:0x100 + delta], b'C' * 0x60)

    def test_build_rewrites_the_archive_offset_table(self):
        result = self._build_archive_block()
        delta = result.validation_report['archive_size_delta']

        count, first, second, third = struct.unpack_from('>4I', result.block, 0)
        self.assertEqual(count, 3)
        self.assertEqual((first, second), (0x20, 0x60))
        self.assertEqual(third, 0xA0 + delta)

    def test_build_rejects_a_model_that_is_not_an_archive_slot(self):
        with self.assertRaisesRegex(ValueError, 'not a populated slot of the archive'):
            self._build_archive_block(model_offset=0x1080, model_length=0x20)

    def test_build_rejects_an_archive_slot_whose_length_disagrees_with_the_schema(self):
        with self.assertRaisesRegex(ValueError, 'ModelLength'):
            self._build_archive_block(model_offset=0x1060, model_length=0x20)

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

    def test_material_alias_patches_texture_and_drawable_states(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            donor = bytearray(0x100)
            struct.pack_into('>I', donor, 0x04, 0x20)
            input_dat.write_bytes(donor)
            data = {'SluggiesModel': {'Submeshes': [{
                'DisplayStates': [
                    {
                        'DisplayStateId': 1,
                        'ShaderModeFieldOffset': '0x34',
                        'ShaderMode': '11110000',
                        'DisplayStateParamBytes': '010203',
                        'MaterialStateAliasedByImporter': True,
                    },
                    {
                        'DisplayStateId': 7,
                        'ShaderModeFieldOffset': '0x44',
                        'ShaderMode': 'Spec',
                        'DisplayStateParamBytes': '040506',
                        'MaterialStateAliasedByImporter': True,
                    },
                ],
            }]}}

            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main.PatchGPLMaterialStates(b'\x00' * 0x40, data, 0)

        self.assertEqual(patched[0x11:0x18], bytes.fromhex('01020311110000'))
        self.assertEqual(patched[0x21:0x28], b'\x04\x05\x06Spec')

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
                    'DisplayStateParamBytes': '000000',
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
                    'DisplayStateParamBytes': '000000',
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
                    'DisplayStateParamBytes': '000000',
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
                    'DisplayStateParamBytes': '000000',
                }],
            }],
        }})

        submesh = parsed.mesh.submeshes[0]
        self.assertEqual(submesh.vertex_data, b'\x01\x02')
        self.assertEqual(submesh.uv_channels[0].uv_data, b'\x05\x06')
        self.assertEqual(submesh.draw_states[0].prim_list_data, b'\xBB')

    def test_topology_rebuild_uses_rebuilt_flush_index_data(self):
        parsed = main.ParseSluggie({'SluggiesModel': {
            'UseBase64': False,
            'SkinData': {
                'QuantizeInfo': 0,
                'SK1s': [],
                'SK2s': [],
                'SKAccs': [],
                'FlushIndSize': 1,
                'FlushIndData': [0xAA, 0xAA],
            },
            'SkinDataEdited': {
                'SK1s': [],
                'SK2s': [],
                'SKAccs': [],
                'FlushIndSize': 2,
                'FlushIndData': [0xBB, 0xBB, 0xCC, 0xCC],
            },
            'Submeshes': [{
                'FacesCount': 0,
                'FacesData': [],
                'FaceTextureIndices': [],
                'VertexBuffer': {
                    'VertexBufferData': [0, 0, 0, 0, 0, 0],
                    'VertexBufferCompCount': 6,
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
                    'DisplayStateParamBytes': '000000',
                }],
            }],
        }})

        # A topology edit (PrimListDataEdited present) must pull flush-index
        # data from SkinDataEdited (produced by GeometryRebuild._rebuild_skinning),
        # not silently fall back to the untouched donor SkinData.
        self.assertEqual(parsed.skinning.flush_ind_size, 2)
        self.assertEqual(parsed.skinning.flush_ind_data, b'\xBB\xBB\xCC\xCC')

    def test_unedited_skinning_still_uses_donor_flush_index_data(self):
        parsed = main.ParseSluggie({'SluggiesModel': {
            'UseBase64': False,
            'SkinData': {
                'QuantizeInfo': 0,
                'SK1s': [],
                'SK2s': [],
                'SKAccs': [],
                'FlushIndSize': 1,
                'FlushIndData': [0xAA, 0xAA],
            },
            'Submeshes': [{
                'FacesCount': 0,
                'FacesData': [],
                'FaceTextureIndices': [],
                'VertexBuffer': {
                    'VertexBufferData': [0, 0, 0, 0, 0, 0],
                    'VertexBufferCompCount': 6,
                    'VertexBufferQuantizeInfo': 0,
                },
                'UVChannels': [],
                'ColorChannels': [],
                'DisplayStates': [{
                    'DisplayStateId': 7,
                    'PrimListData': [0xAA],
                    'ShaderMode': '00000000',
                    'PrimListPtrFieldOffset': '0x0',
                    'PrimListSizeFieldOffset': '0x0',
                    'PrimListAbsoluteOffset': '0x0',
                    'PrimListLength': 1,
                    'DisplayStateParamBytes': '000000',
                }],
            }],
        }})

        self.assertEqual(parsed.skinning.flush_ind_size, 1)
        self.assertEqual(parsed.skinning.flush_ind_data, b'\xAA\xAA')

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
        unaligned_tail_offset = skn_offset + len(b'SKN')
        tail_offset = -(-unaligned_tail_offset // 32) * 32  # F10: tail starts 32-aligned
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
        route_events = []
        with (
            mock.patch.object(main.hh, 'readOutputDolEntry', return_value=(0, 42)),
            mock.patch.object(main.hh, 'routedHammerspaceRanges', return_value=[]),
            mock.patch.object(main.hh, 'findFreeMemoryChunk', return_value=0x2000),
            mock.patch.object(main.hh, 'writeModelBlock') as write_model,
            mock.patch.object(
                main.hh, 'patchDolEntry',
                side_effect=lambda *_args: route_events.append('patch'),
            ) as patch_dol,
            mock.patch.object(
                main.hh, 'findSharedEntries',
                side_effect=lambda *_args: route_events.append('find') or [(19, 1)],
            ),
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
        self.assertEqual(route_events, ['find', 'patch', 'patch'])
        patch_fst.assert_called_once_with(123456)
        zero_original.assert_called_once_with(18, 0)
        write_dumps.assert_called_once_with('fixture.sluggie', 0x1000, 42, b'model-block')

    def test_write_operation_accepts_explicit_aligned_destination(self):
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
        destination = 0x40000000
        with (
            mock.patch.object(main.hh, 'readOutputDolEntry', return_value=(0, 42)),
            mock.patch.object(main.hh, 'routedHammerspaceRanges', return_value=[]),
            mock.patch.object(main.hh, 'findFreeMemoryChunk') as find_free,
            mock.patch.object(main.hh, 'writeModelBlock') as write_model,
            mock.patch.object(main.hh, 'patchDolEntry'),
            mock.patch.object(main.hh, 'findSharedEntries', return_value=[]),
            mock.patch.object(main.hh, 'patchFstFileSize'),
            mock.patch.object(main.hh, 'zeroOriginalModel'),
            mock.patch.object(main.hh, 'writeDebugDumps'),
            mock.patch.object(main.os.path, 'getsize', return_value=destination + 1024),
        ):
            new_offset = main.WriteModelBlock(build, 'fixture.sluggie', destination)

        self.assertEqual(new_offset, destination)
        find_free.assert_not_called()
        write_model.assert_called_once_with(b'model-block', destination)

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
            mock.patch.object(main.hh, 'routedHammerspaceRanges', return_value=[]),
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

    def test_replacement_keeps_old_block_until_new_route_commits(self):
        build = main.ModelBlockBuild(
            block=b'new-model-block',
            parsed=self.parsed,
            chunk_number=18,
            file_index=0,
            original_offset=0x1000,
            original_length=42,
            section_modes=main.SectionModes(),
            section_sizes={},
            validation_report={'valid': True},
        )
        events = []
        with (
            mock.patch.object(main.hh, 'BASE_SIZE', 0x2000),
            mock.patch.object(main.hh, 'OUTPUT_DAT', 'output.dat'),
            mock.patch.object(main.hh, 'readOutputDolEntry', return_value=(0x3000, 100)),
            mock.patch.object(main.hh, 'routedHammerspaceRanges', return_value=[]),
            mock.patch.object(main.hh, 'findFreeMemoryChunk', return_value=0x4000),
            mock.patch.object(main.hh, 'findSharedEntries', return_value=[(19, 1)]),
            mock.patch.object(
                main.hh, 'writeModelBlock',
                side_effect=lambda *_args: events.append('write'),
            ),
            mock.patch.object(
                main.hh, 'patchDolEntry',
                side_effect=lambda *_args: events.append('route'),
            ),
            mock.patch.object(main.hh, 'patchFstFileSize'),
            mock.patch.object(
                main.hh, 'zeroOriginalModel',
                side_effect=lambda *_args: events.append('zero-original'),
            ),
            mock.patch.object(
                main.hh, 'zeroRange',
                side_effect=lambda *_args: events.append('zero-old'),
            ) as zero_range,
            mock.patch.object(main.hh, 'writeDebugDumps'),
            mock.patch.object(main.os.path, 'getsize', return_value=123456),
        ):
            new_offset = main.WriteModelBlock(build, 'fixture.sluggie')

        self.assertEqual(new_offset, 0x4000)
        self.assertEqual(
            events,
            ['write', 'route', 'route', 'zero-original', 'zero-old'],
        )
        zero_range.assert_called_once_with(0x3000, 100)

    def test_replacement_write_failure_preserves_existing_route(self):
        build = main.ModelBlockBuild(
            block=b'new-model-block',
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
            mock.patch.object(main.hh, 'BASE_SIZE', 0x2000),
            mock.patch.object(main.hh, 'readOutputDolEntry', return_value=(0x3000, 100)),
            mock.patch.object(main.hh, 'routedHammerspaceRanges', return_value=[]),
            mock.patch.object(main.hh, 'findFreeMemoryChunk', return_value=0x4000),
            mock.patch.object(main.hh, 'findSharedEntries', return_value=[]),
            mock.patch.object(main.hh, 'writeModelBlock', side_effect=IOError('verify failed')),
            mock.patch.object(main.hh, 'patchDolEntry') as patch_dol,
            mock.patch.object(main.hh, 'zeroRange') as zero_range,
        ):
            with self.assertRaisesRegex(IOError, 'verify failed'):
                main.WriteModelBlock(build, 'fixture.sluggie')

        patch_dol.assert_not_called()
        zero_range.assert_not_called()

    def test_replacement_reserves_routed_and_live_ranges_during_free_search(self):
        build = main.ModelBlockBuild(
            block=b'new-model-block',
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
            mock.patch.object(main.hh, 'BASE_SIZE', 0x2000),
            mock.patch.object(main.hh, 'readOutputDolEntry', return_value=(0x3000, 100)),
            mock.patch.object(main.hh, 'routedHammerspaceRanges', return_value=[(0x5000, 16)]),
            mock.patch.object(main.hh, 'findFreeMemoryChunk', return_value=0x4000) as find_free,
            mock.patch.object(main.hh, 'findSharedEntries', return_value=[]),
            mock.patch.object(main.hh, 'writeModelBlock'),
            mock.patch.object(main.hh, 'patchDolEntry'),
            mock.patch.object(main.hh, 'patchFstFileSize'),
            mock.patch.object(main.hh, 'zeroOriginalModel'),
            mock.patch.object(main.hh, 'zeroRange'),
            mock.patch.object(main.hh, 'writeDebugDumps'),
            mock.patch.object(main.os.path, 'getsize', return_value=123456),
        ):
            main.WriteModelBlock(build, 'fixture.sluggie')

        find_free.assert_called_once_with(
            len(build.block), reserved_ranges=[(0x5000, 16), (0x3000, 100)],
        )

    def test_replacement_refuses_new_block_overlapping_the_live_block(self):
        # Regression: the free search once placed a new block over the zero
        # tail of the block it replaced, and zeroing the old range then wiped
        # the new block's header.
        build = main.ModelBlockBuild(
            block=b'n' * 64,
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
            mock.patch.object(main.hh, 'BASE_SIZE', 0x2000),
            mock.patch.object(main.hh, 'readOutputDolEntry', return_value=(0x3000, 100)),
            mock.patch.object(main.hh, 'routedHammerspaceRanges', return_value=[]),
            mock.patch.object(main.hh, 'findFreeMemoryChunk', return_value=0x3000 + 100 - 32),
            mock.patch.object(main.hh, 'writeModelBlock') as write_model,
            mock.patch.object(main.hh, 'patchDolEntry') as patch_dol,
            mock.patch.object(main.hh, 'zeroRange') as zero_range,
        ):
            with self.assertRaisesRegex(RuntimeError, 'overlaps the live block'):
                main.WriteModelBlock(build, 'fixture.sluggie')

        write_model.assert_not_called()
        patch_dol.assert_not_called()
        zero_range.assert_not_called()

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
            custom_submeshes=[],
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
        # descriptor table, and every individual payload start is likewise
        # 32-byte aligned (F10), matching vanilla TEX sections.
        data_start = (4 + 2 * 0x20 + 31) & ~31  # 0x60
        self.assertEqual(data_start % 32, 0)
        self.assertEqual(img0_off, data_start)
        self.assertEqual(img1_off, data_start + 64)  # 64 already 32-aligned
        self.assertEqual(pal0_off, data_start + 64 + 32)  # 32 already 32-aligned
        self.assertEqual(pal1_off, data_start + 64 + 32 + 32)  # 4 padded up to 32
        expected_len = data_start + 64 + 32 + 32 + 32  # 2 padded up to 32
        self.assertEqual(len(section), expected_len)

        # All offsets are themselves 32-byte aligned and within the section.
        for off, length in (
            (img0_off, 64), (img1_off, 32), (pal0_off, 4), (pal1_off, 2),
        ):
            self.assertEqual(off % 32, 0)
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

    def test_build_tex_aligns_data_region_with_naturally_aligned_payloads(self):
        """The TEX data region starts aligned; these payload sizes preserve it."""
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

        # These naturally aligned image payloads keep every pointer aligned.
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

    def test_build_tex_pads_each_payload_to_32_byte_boundary(self):
        # F10: every image/palette payload start is 32-byte aligned, even
        # though an 8-byte payload leaves the next one at 8 mod 32 if packed
        # back-to-back with no padding.
        textures = [
            self._make_texture(index, width=1, height=1, fmt=0xE)
            for index in range(2)
        ]
        parsed = self._make_parsed(textures)
        entries = tuple(
            texture_helper.TexturePlanEntry(
                texture_index=index,
                texture_file_name=f'{index}.png',
                width=1,
                height=1,
                format=0xE,
                format_name='CMPR',
                image_data=bytes([index + 1]) * 8,
                palette_data=b'',
                palette_entries=0,
                palette_format=None,
            )
            for index in range(2)
        )

        section = main.BuildTEX(parsed, texture_helper.TexturePlan(entries=entries))

        data_start = (4 + 2 * 0x20 + 31) & ~31
        first_ptr = struct.unpack_from('>I', section, 4)[0]
        second_ptr = struct.unpack_from('>I', section, 4 + 0x20)[0]
        self.assertEqual(first_ptr, data_start)
        self.assertEqual(second_ptr, data_start + 32)
        self.assertEqual(second_ptr % 32, 0)
        self.assertEqual(section[first_ptr:first_ptr + 8], b'\x01' * 8)
        # The gap between the first payload and the next aligned boundary is
        # zero padding.
        self.assertEqual(section[first_ptr + 8:second_ptr], b'\x00' * 24)
        self.assertEqual(section[second_ptr:second_ptr + 8], b'\x02' * 8)

    def test_build_tex_appends_plan_entry_from_donor_template(self):
        donor = self._make_texture(
            0, width=1, height=1, fmt=0xE,
            image_offset=0x100, image_length=8,
        )
        donor.desc_unknown_at_10 = b'ABCDEF\x00'
        donor.desc_unknown_at_1b = b'12345'
        parsed = self._make_parsed([donor])
        addition = texture_helper.TexturePlanEntry(
            texture_index=1,
            texture_file_name='new.png',
            width=5,
            height=5,
            format=0xE,
            format_name='CMPR',
            image_data=b'\xAA' * 32,
            palette_data=b'',
            palette_entries=0,
            palette_format=None,
            template_texture_index=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            buf = bytearray(0x200)
            buf[0x100:0x108] = b'\xCD' * 8
            input_dat.write_bytes(bytes(buf))
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                section = main.BuildTEX(
                    parsed, texture_helper.TexturePlan(entries=(addition,))
                )

        self.assertEqual(struct.unpack_from('>H', section, 0)[0], 2)
        appended_desc = 4 + 0x20
        self.assertEqual(struct.unpack_from('>HH', section, appended_desc + 8), (5, 5))
        self.assertEqual(section[appended_desc + 0x10:appended_desc + 0x17], b'ABCDEF\x00')
        self.assertEqual(section[appended_desc + 0x1B:appended_desc + 0x20], b'12345')
        appended_ptr = struct.unpack_from('>I', section, appended_desc)[0]
        self.assertEqual(section[appended_ptr:appended_ptr + 32], b'\xAA' * 32)

    def test_build_tex_rejects_malformed_addition_plan(self):
        parsed = self._make_parsed([
            self._make_texture(0, width=1, height=1, fmt=0xE),
        ])

        def addition(index, template=0, fmt=0xE):
            return texture_helper.TexturePlanEntry(
                texture_index=index,
                texture_file_name='new.png',
                width=1,
                height=1,
                format=fmt,
                format_name='CMPR',
                image_data=b'\xAA' * 8,
                palette_data=b'',
                palette_entries=0,
                palette_format=None,
                template_texture_index=template,
            )

        with self.assertRaisesRegex(ValueError, 'must be contiguous'):
            main.BuildTEX(parsed, texture_helper.TexturePlan(entries=(addition(2),)))
        with self.assertRaisesRegex(ValueError, 'invalid template texture'):
            main.BuildTEX(parsed, texture_helper.TexturePlan(entries=(addition(1, 4),)))
        with self.assertRaisesRegex(ValueError, 'does not match template format'):
            main.BuildTEX(parsed, texture_helper.TexturePlan(entries=(addition(1, fmt=0x6),)))
        with self.assertRaisesRegex(ValueError, 'duplicate texture plan index'):
            main.BuildTEX(
                parsed,
                texture_helper.TexturePlan(entries=(addition(1), addition(1))),
            )


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
            png_overrides=None,
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

    def test_tex_build_with_png_override_passes_overrides(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': '0.png'},
            ],
        })
        overrides = {0: '/tmp/edited.png'}
        plan = mock.Mock(skipped=())
        with (
            mock.patch.object(
                texture_helper, 'build_hammerspace_texture_plan',
                return_value=plan,
            ) as build_plan,
            mock.patch.object(main, 'BuildTEX', return_value=b'BUILT_TEX') as build_tex,
            mock.patch.object(main, 'CloneTEX') as clone_tex,
        ):
            result = self._run(
                main.SectionModes(tex='build'),
                sluggie_path='model.sluggies',
                tex_png_overrides=overrides,
            )

        build_plan.assert_called_once_with(
            'model.sluggies',
            self.data['SluggiesModel']['TextureDescriptors'],
            allow_dimension_change=True,
            png_overrides=overrides,
        )
        build_tex.assert_called_once_with(self.parsed, plan)
        clone_tex.assert_not_called()
        self.assertEqual(result.section_sizes['TEX'], len(b'BUILT_TEX'))

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

    def test_tex_build_plans_additional_texture_descriptors(self):
        additional = [{
            'TextureFileName': 'new.png',
            'TemplateTextureIndex': 0,
        }]
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'ReimportTextures': True,
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': '0.png'},
            ],
            'AdditionalTextureDescriptors': additional,
        })
        plan = mock.Mock(skipped=())
        with (
            mock.patch.object(
                texture_helper, 'build_hammerspace_texture_plan', return_value=plan,
            ) as build_plan,
            mock.patch.object(main, 'BuildTEX', return_value=b'BUILT_TEX') as build_tex,
        ):
            self._run(main.SectionModes(tex='build'), sluggie_path='model.sluggies')

        build_plan.assert_called_once_with(
            'model.sluggies',
            self.data['SluggiesModel']['TextureDescriptors'],
            allow_dimension_change=True,
            png_overrides=None,
            additional_descriptors=(
                texture_helper.AdditionalTextureDescriptor('new.png', 0),
            ),
        )
        build_tex.assert_called_once_with(self.parsed, plan)

    def test_tex_plan_built_before_gpl_append_resolves_additional_texture_index(self):
        """PLAN_AddSubmesh.md Phase 4 step 2: the TEX plan is resolved before
        the GPL append, so PatchGPLAppendSubmesh receives the custom
        submesh's real, final TEX index instead of a placeholder."""
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'ReimportTextures': True,
            'TextureDescriptors': [{'TextureIndex': 0, 'TextureFileName': '0.png'}],
            'AdditionalTextureDescriptors': [{'TextureFileName': 'new.png', 'TemplateTextureIndex': 0}],
            'CustomSubmeshes': [{
                'CustomSubmeshId': 'custom0',
                'HostBoneId': 1,
                'TextureAssignment': {'AdditionalTextureFileName': 'new.png'},
            }],
        })
        cs = SimpleNamespace(
            custom_submesh_id='custom0',
            texture_assignment=SimpleNamespace(
                donor_texture_index=None, additional_texture_file_name='new.png',
            ),
        )
        self.parsed = SimpleNamespace(custom_submeshes=[cs])
        plan_entry = mock.Mock(texture_file_name='new.png', texture_index=5)
        plan = mock.Mock(skipped=(), entries=(plan_entry,))
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, '_validate_hammerspace_contract'),
                mock.patch.object(main, '_validate_custom_submeshes'),
                mock.patch.object(
                    texture_helper, 'build_hammerspace_texture_plan', return_value=plan,
                ),
                mock.patch.object(main, 'BuildTEX', return_value=b'BUILT_TEX'),
                mock.patch.object(main, 'PatchGPLAppendSubmesh', return_value=b'GPL2') as patch_append,
                mock.patch.object(main, '_apply_geo_id_patches', side_effect=lambda act, *a, **k: act),
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {'GPL': 32, 'ACT': 35, 'TEX': 38, 'SKN': 41}},
                }),
            ):
                main.BuildModelBlock(
                    self.data, main.SectionModes(gpl='build', tex='build'),
                    sluggie_path='model.sluggies',
                )

        patch_append.assert_called_once_with(
            b'GPL', self.data['SluggiesModel'], self.parsed, {'new.png': 5},
        )

    def test_custom_submesh_additional_texture_file_name_requires_tex_build_and_reimport(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'CustomSubmeshes': [{'TextureAssignment': {'AdditionalTextureFileName': 'new.png'}}],
        })
        with (
            mock.patch.object(main, '_validate_hammerspace_contract'),
            mock.patch.object(main, '_validate_custom_submeshes'),
        ):
            with self.assertRaisesRegex(ValueError, 'AdditionalTextureFileName'):
                main.BuildModelBlock(
                    self.data, main.SectionModes(gpl='build', tex='clone'),
                    sluggie_path='model.sluggies',
                )

    def test_additional_texture_descriptors_require_reimport_enabled(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': '0.png'},
            ],
            'AdditionalTextureDescriptors': [{
                'TextureFileName': 'new.png',
                'TemplateTextureIndex': 0,
            }],
        })
        with self.assertRaisesRegex(ValueError, "require 'ReimportTextures'"):
            self._run(main.SectionModes(tex='build'), sluggie_path='model.sluggies')

    def test_tex_build_accepts_caller_supplied_plan(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': '0.png'},
            ],
        })
        plan = mock.Mock(skipped=())
        with (
            mock.patch.object(texture_helper, 'build_hammerspace_texture_plan') as build_plan,
            mock.patch.object(main, 'BuildTEX', return_value=b'BUILT_TEX') as build_tex,
        ):
            result = self._run(
                main.SectionModes(tex='build'),
                sluggie_path='model.sluggies',
                texture_plan=plan,
            )

        build_plan.assert_not_called()
        build_tex.assert_called_once_with(self.parsed, plan)
        self.assertEqual(result.section_sizes['TEX'], len(b'BUILT_TEX'))

    def test_tex_build_rejects_supplied_plan_with_automatic_reimport(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'ReimportTextures': True,
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': '0.png'},
            ],
        })
        with self.assertRaisesRegex(ValueError, 'caller-supplied texture_plan'):
            self._run(
                main.SectionModes(tex='build'),
                sluggie_path='model.sluggies',
                texture_plan=mock.Mock(skipped=()),
            )

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


class ApplyGeoIdPatchesTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 3 step 1: ``_apply_geo_id_patches`` writes
    ``GeoId = <new submesh index>`` onto each custom submesh's host bone at
    its ACT-section-relative ``GeoIdFieldOffset``.

    Follows the same shape as ``BuildModelBlockRootScaleTests``: only the ACT
    section payload is mocked, and the real ``_act_section_absolute`` reads
    the model-block header from a temp INPUT_DAT.
    """

    SOURCE_MODEL_OFFSET = 0x1000
    ACT_OFF = 0x40  # model header +0x08 -> ACT section rel. to model start

    def _write_input_dat(self, temp_dir, act_off):
        input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
        buf = bytearray(0x2000)
        struct.pack_into('>I', buf, self.SOURCE_MODEL_OFFSET + 0x08, act_off)
        input_dat.write_bytes(bytes(buf))
        return input_dat

    def _bone(self, bone_id, field_off_relative, geo_raw=0xFFFF):
        field_off_absolute = self.SOURCE_MODEL_OFFSET + self.ACT_OFF + field_off_relative
        return {
            'BoneId': bone_id,
            'GeoIdRaw': geo_raw,
            'GeoIdFieldOffset': f'0x{field_off_absolute:X}',
        }

    def _custom_submesh(self, cs_id, host_bone_id):
        return {'CustomSubmeshId': cs_id, 'HostBoneId': host_bone_id}

    def test_writes_new_submesh_index_at_act_relative_offset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\xff' * 0x80
            data = {'SluggiesModel': {
                'Submeshes': [{}, {}],
                'BoneHierarchy': [self._bone(5, 0x20)],
                'CustomSubmeshes': [self._custom_submesh('custom0', 5)],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)

        self.assertEqual(patched[0x20:0x22], struct.pack('>H', 2))
        self.assertEqual(patched[:0x20], act_bytes[:0x20])
        self.assertEqual(patched[0x22:], act_bytes[0x22:])

    def test_multiple_custom_submeshes_index_in_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\xff' * 0x80
            data = {'SluggiesModel': {
                'Submeshes': [{}],
                'BoneHierarchy': [self._bone(5, 0x20), self._bone(6, 0x28)],
                'CustomSubmeshes': [
                    self._custom_submesh('custom0', 5),
                    self._custom_submesh('custom1', 6),
                ],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)

        self.assertEqual(patched[0x20:0x22], struct.pack('>H', 1))
        self.assertEqual(patched[0x28:0x2A], struct.pack('>H', 2))

    def test_no_custom_submeshes_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\xff' * 0x80
            data = {'SluggiesModel': {'Submeshes': [], 'BoneHierarchy': []}}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                result = main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)
        self.assertEqual(result, act_bytes)

    def test_no_act_section_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, 0)  # act_off == 0 -> no ACT
            act_bytes = b'\xff' * 0x80
            data = {'SluggiesModel': {
                'Submeshes': [{}],
                'BoneHierarchy': [self._bone(5, 0x20)],
                'CustomSubmeshes': [self._custom_submesh('custom0', 5)],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                result = main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)
        self.assertEqual(result, act_bytes)

    def test_missing_geo_id_field_offset_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\xff' * 0x80
            bone = self._bone(5, 0x20)
            del bone['GeoIdFieldOffset']
            data = {'SluggiesModel': {
                'Submeshes': [{}],
                'BoneHierarchy': [bone],
                'CustomSubmeshes': [self._custom_submesh('custom0', 5)],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                with self.assertRaises(ValueError):
                    main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)

    def test_stale_geo_id_field_raises(self):
        # The field doesn't read 0xFFFF (e.g. already claimed) -> reject
        # rather than silently overwrite.
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = bytearray(b'\xff' * 0x80)
            struct.pack_into('>H', act_bytes, 0x20, 0x0003)
            # Also poison the off-by-8 fallback location so it can't mask
            # this as the pre-fix export.py bug.
            struct.pack_into('>H', act_bytes, 0x28, 0x0000)
            data = {'SluggiesModel': {
                'Submeshes': [{}],
                'BoneHierarchy': [self._bone(5, 0x20)],
                'CustomSubmeshes': [self._custom_submesh('custom0', 5)],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                with self.assertRaises(ValueError):
                    main._apply_geo_id_patches(bytes(act_bytes), data, self.SOURCE_MODEL_OFFSET)

    def test_field_offset_before_act_section_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\xff' * 0x80
            bone = self._bone(5, 0x20)
            bone['GeoIdFieldOffset'] = f'0x{self.SOURCE_MODEL_OFFSET + self.ACT_OFF - 1:X}'
            data = {'SluggiesModel': {
                'Submeshes': [{}],
                'BoneHierarchy': [bone],
                'CustomSubmeshes': [self._custom_submesh('custom0', 5)],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                with self.assertRaises(ValueError):
                    main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)

    # -- PLAN_AddSubmesh.md Phase 3 step 2: GeoIdEdited retargets --------

    def test_geo_id_edited_writes_target_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = bytearray(b'\xff' * 0x80)
            struct.pack_into('>H', act_bytes, 0x20, 0x0001)  # bone 5 currently owns submesh 1
            bone = self._bone(5, 0x20, geo_raw=0x0001)
            bone['GeoIdEdited'] = 0xFFFF  # freed by a retarget
            data = {'SluggiesModel': {
                'Submeshes': [{}, {}],
                'BoneHierarchy': [bone],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main._apply_geo_id_patches(bytes(act_bytes), data, self.SOURCE_MODEL_OFFSET)
        self.assertEqual(patched[0x20:0x22], struct.pack('>H', 0xFFFF))

    def test_geo_id_edited_no_op_when_equal_to_original_leaves_bytes_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = bytearray(b'\xff' * 0x80)
            struct.pack_into('>H', act_bytes, 0x20, 0x0001)
            bone = self._bone(5, 0x20, geo_raw=0x0001)
            bone['GeoIdEdited'] = 0x0001  # same as original -> no-op
            data = {'SluggiesModel': {
                'Submeshes': [{}, {}],
                'BoneHierarchy': [bone],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main._apply_geo_id_patches(bytes(act_bytes), data, self.SOURCE_MODEL_OFFSET)
        self.assertEqual(patched, bytes(act_bytes))

    def test_geo_id_edited_missing_field_offset_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\xff' * 0x80
            bone = {'BoneId': 5, 'GeoIdRaw': 0xFFFF, 'GeoIdEdited': 3}
            data = {'SluggiesModel': {'Submeshes': [{}], 'BoneHierarchy': [bone]}}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                with self.assertRaises(ValueError):
                    main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)

    def test_geo_id_edited_out_of_range_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\xff' * 0x80
            bone = self._bone(5, 0x20)
            bone['GeoIdEdited'] = 0x10000
            data = {'SluggiesModel': {'Submeshes': [{}], 'BoneHierarchy': [bone]}}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                with self.assertRaises(ValueError):
                    main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)

    def test_geo_id_edited_frees_bone_that_custom_submesh_then_claims(self):
        # A retarget frees bone 5 (GeoIdEdited 0xFFFF) in the same build that
        # a custom submesh claims it. Retargets are written first, so the
        # custom-submesh claim's "must currently read 0xFFFF" check sees the
        # freshly-freed field and succeeds.
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = bytearray(b'\xff' * 0x80)
            struct.pack_into('>H', act_bytes, 0x20, 0x0001)
            bone = self._bone(5, 0x20, geo_raw=0x0001)
            bone['GeoIdEdited'] = 0xFFFF
            data = {'SluggiesModel': {
                'Submeshes': [{}, {}],
                'BoneHierarchy': [bone],
                'CustomSubmeshes': [self._custom_submesh('custom0', 5)],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main._apply_geo_id_patches(bytes(act_bytes), data, self.SOURCE_MODEL_OFFSET)
        # donor_count (2) + index 0 -> new submesh index 2
        self.assertEqual(patched[0x20:0x22], struct.pack('>H', 2))

    def test_geo_id_edited_retarget_onto_a_bone_blocks_it_from_custom_claim(self):
        # A retarget assigns bone 6 away from 0xFFFF (it now owns submesh 1)
        # in the same build that a custom submesh tries to claim it -- the
        # claim must fail rather than silently overwrite the retarget.
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = bytearray(b'\xff' * 0x80)
            # Poison the off-by-8 fallback location too, so it can't mask
            # this as the pre-fix export.py bug.
            struct.pack_into('>H', act_bytes, 0x28 + 8, 0x0000)
            bone = self._bone(6, 0x28, geo_raw=0xFFFF)
            bone['GeoIdEdited'] = 1
            data = {'SluggiesModel': {
                'Submeshes': [{}, {}],
                'BoneHierarchy': [bone],
                'CustomSubmeshes': [self._custom_submesh('custom0', 6)],
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                with self.assertRaises(ValueError):
                    main._apply_geo_id_patches(bytes(act_bytes), data, self.SOURCE_MODEL_OFFSET)

    def test_no_geo_id_edits_or_custom_submeshes_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\xff' * 0x80
            bone = self._bone(5, 0x20)  # GeoIdEdited not set
            data = {'SluggiesModel': {'Submeshes': [{}], 'BoneHierarchy': [bone]}}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                result = main._apply_geo_id_patches(act_bytes, data, self.SOURCE_MODEL_OFFSET)
        self.assertEqual(result, act_bytes)


class ParseSluggieCustomSubmeshesTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 1 step 2: ParseSluggie produces a parsed
    custom-submesh object without touching donor structures."""

    def test_no_custom_submeshes_yields_empty_list(self):
        parsed = main.ParseSluggie({'SluggiesModel': {'UseBase64': False}})
        self.assertEqual(parsed.custom_submeshes, [])

    def test_full_entry_parses_without_touching_donor_structures(self):
        data = {'SluggiesModel': {
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
                'DisplayStates': [],
            }],
            'CustomSubmeshes': [{
                'CustomSubmeshId': 'custom0',
                'MeshName': 'CustomSubmesh_0',
                'HostBoneId': 49,
                'TemplateSource': 'rigid:sm1_ds5',
                'VertexBufferData': [1, 2, 3, 4, 5, 6],
                'NormalBufferData': [7, 8, 9, 10, 11, 12],
                'NormalFacesData': [0, 0, 0],
                'ColorChannelData': [255, 255, 255, 255],
                'ColorFacesData': [0, 0, 0],
                'UVChannels': [{
                    'UVChannelIndex': 0,
                    'UVChannelData': [0, 0, 1, 1],
                    'UVFacesData': [0, 0, 0],
                }],
                'FacesCount': 1,
                'FacesData': [0, 1, 2],
                'TextureAssignment': {'DonorTextureIndex': 0},
            }],
        }}
        parsed = main.ParseSluggie(data)

        self.assertEqual(len(parsed.custom_submeshes), 1)
        cs = parsed.custom_submeshes[0]
        self.assertEqual(cs.custom_submesh_id, 'custom0')
        self.assertEqual(cs.mesh_name, 'CustomSubmesh_0')
        self.assertEqual(cs.host_bone_id, 49)
        self.assertEqual(cs.template_source, 'rigid:sm1_ds5')
        self.assertEqual(cs.vertex_data, bytes([1, 2, 3, 4, 5, 6]))
        self.assertEqual(cs.normal_data, bytes([7, 8, 9, 10, 11, 12]))
        self.assertEqual(cs.normal_faces_data, bytes([0, 0, 0]))
        self.assertEqual(cs.color_data, bytes([255, 255, 255, 255]))
        self.assertEqual(cs.color_faces_data, bytes([0, 0, 0]))
        self.assertEqual(len(cs.uv_channels), 1)
        self.assertEqual(cs.uv_channels[0].channel_index, 0)
        self.assertEqual(cs.uv_channels[0].uv_data, bytes([0, 0, 1, 1]))
        self.assertEqual(cs.uv_channels[0].uv_faces_data, bytes([0, 0, 0]))
        self.assertEqual(cs.faces_count, 1)
        self.assertEqual(cs.faces_data, bytes([0, 1, 2]))
        self.assertEqual(cs.texture_assignment.donor_texture_index, 0)
        self.assertIsNone(cs.texture_assignment.additional_texture_file_name)
        # No VertexBufferQuantizeInfo in the entry: the historical fixed value.
        self.assertEqual(cs.vertex_quantize_info, 59)

        # Donor mesh data must be untouched by the presence of CustomSubmeshes.
        self.assertEqual(len(parsed.mesh.submeshes), 1)
        self.assertEqual(parsed.mesh.submeshes[0].faces_count, 0)

    def test_position_quantize_info_is_carried_through_to_the_blob_header(self):
        data = {'SluggiesModel': {
            'UseBase64': False,
            'CustomSubmeshes': [{
                'CustomSubmeshId': 'custom0',
                'MeshName': 'CustomSubmesh_0',
                'HostBoneId': 49,
                'TemplateSource': 'builtin:rigid_spec_v1',
                'VertexBufferData': [0, 0, 0, 0, 0, 0],
                'VertexBufferQuantizeInfo': 54,
                'UVChannels': [],
                'FacesCount': 0,
                'FacesData': [],
                'TextureAssignment': {'DonorTextureIndex': 0},
            }],
        }}
        cs = main.ParseSluggie(data).custom_submeshes[0]
        self.assertEqual(cs.vertex_quantize_info, 54)
        submesh = main._custom_submesh_to_submesh(cs, 3, [], 0, b'')
        self.assertEqual(submesh.vertex_comp_count, 3)
        self.assertEqual(submesh.vertex_quantize_info, 54)

    def test_texture_assignment_by_additional_texture_file_name(self):
        data = {'SluggiesModel': {
            'UseBase64': False,
            'CustomSubmeshes': [{
                'CustomSubmeshId': 'custom0',
                'MeshName': 'CustomSubmesh_0',
                'HostBoneId': 49,
                'TemplateSource': 'builtin:rigid_spec_v1',
                'VertexBufferData': [0, 0, 0, 0, 0, 0],
                'UVChannels': [],
                'FacesCount': 0,
                'FacesData': [],
                'TextureAssignment': {'AdditionalTextureFileName': 'custom_custom0.png'},
            }],
        }}
        parsed = main.ParseSluggie(data)
        cs = parsed.custom_submeshes[0]
        self.assertIsNone(cs.texture_assignment.donor_texture_index)
        self.assertEqual(cs.texture_assignment.additional_texture_file_name, 'custom_custom0.png')
        self.assertIsNone(cs.normal_data)
        self.assertIsNone(cs.color_data)


def _ds(surface_id, state_id, mode, pad='000000', prim=0):
    return {
        'SurfaceId': surface_id,
        'DisplayStateId': state_id,
        'DisplayStateParamBytes': pad,
        'ShaderMode': mode,
        'PrimListLength': prim,
        'PrimListData': 'AAA=' if prim else '',
        'FaceCount': 1 if prim else 0,
    }


def _validation_bone(bone_id, geo_raw=0xFFFF, parent=0):
    return {'BoneId': bone_id, 'GeoIdRaw': geo_raw, 'ParentBoneId': parent, 'GeoIdFieldOffset': '0x1000'}


def _validation_base_model():
    """A minimal donor with one skinned submesh 0 and one rigid submesh 1.

    submesh0 carries a plain Spec surface (sm0_ds4), a hand-role RhSp surface
    (sm0_ds5) and a rejected Type-6 00000375 surface (sm0_ds7) so `derived:`
    checks have something to reject. submesh1 mirrors the same shape for
    `rigid:` checks (sm1_ds4 = Spec, sm1_ds5 = RhSp). Bones 1 and 3 are free
    host candidates; bone 2 already owns submesh 1.
    """
    return {
        'UseBase64': True,
        'UseHammerspace': True,
        'BoneHierarchy': [
            _validation_bone(0, parent=None),
            _validation_bone(1),
            _validation_bone(2, geo_raw=1),
            _validation_bone(3),
        ],
        'Submeshes': [
            {
                'MeshName': 'body',
                'VertexBuffer': {'VertexBufferCompCount': 6, 'VertexBufferQuantizeInfo': 59, 'VertexBufferData': 'AAA='},
                'DisplayStates': [
                    _ds('sm0_ds0', 1, '11110000', '000008'),
                    _ds('sm0_ds1', 4, 'fffffff0'),
                    _ds('sm0_ds2', 3, '00003cbc'),
                    _ds('sm0_ds3', 6, '00000374', '010000'),
                    _ds('sm0_ds4', 7, 'Spec', '320064', prim=32),
                    _ds('sm0_ds5', 7, 'RhSp', '460064', prim=32),
                    _ds('sm0_ds6', 6, '00000375', '010000'),
                    _ds('sm0_ds7', 7, 'Spec', '320064', prim=32),
                ],
            },
            {
                'MeshName': 'head',
                'VertexBuffer': {'VertexBufferCompCount': 3, 'VertexBufferQuantizeInfo': 59, 'VertexBufferData': 'AAA='},
                'DisplayStates': [
                    _ds('sm1_ds0', 1, '11110000', '000008'),
                    _ds('sm1_ds1', 4, 'ffffff10'),
                    _ds('sm1_ds2', 3, '000028a8'),
                    _ds('sm1_ds3', 6, '00000374', '010000'),
                    _ds('sm1_ds4', 7, 'Spec', '640064', prim=32),
                    _ds('sm1_ds5', 7, 'RhSp', '640064', prim=32),
                ],
            },
        ],
    }


def _validation_entry(**overrides):
    entry = {
        'CustomSubmeshId': 'custom0',
        'MeshName': 'CustomSubmesh_0',
        'HostBoneId': 1,
        'TemplateSource': 'rigid:sm1_ds4',
        'VertexBufferData': base64.b64encode(bytes(6)).decode('ascii'),
        'UVChannels': [{
            'UVChannelIndex': 0,
            'UVChannelData': base64.b64encode(bytes(4)).decode('ascii'),
            'UVFacesData': base64.b64encode(struct.pack('>3H', 0, 0, 0)).decode('ascii'),
        }],
        'FacesCount': 1,
        'FacesData': base64.b64encode(struct.pack('>3H', 0, 0, 0)).decode('ascii'),
        'TextureAssignment': {'DonorTextureIndex': 0},
    }
    entry.update(overrides)
    return entry


class ValidateCustomSubmeshesTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 1 step 3: reject an invalid CustomSubmeshes
    entry before any DAT/DOL write, with each error naming the entry."""

    def test_no_entries_is_a_noop(self):
        model = _validation_base_model()
        main._validate_custom_submeshes(model)  # must not raise

    def test_missing_hammerspace_flag_is_rejected(self):
        model = _validation_base_model()
        model['UseHammerspace'] = False
        model['CustomSubmeshes'] = [_validation_entry()]
        with self.assertRaisesRegex(ValueError, 'Hammerspace Mode'):
            main._validate_custom_submeshes(model)

    def test_valid_rigid_entry_passes(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry()]
        main._validate_custom_submeshes(model)

    def test_valid_derived_entry_passes(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='derived:sm0_ds4')]
        main._validate_custom_submeshes(model)

    def test_valid_builtin_entry_passes(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='builtin:rigid_spec_v1')]
        main._validate_custom_submeshes(model)

    def test_unknown_host_bone_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(HostBoneId=999)]
        with self.assertRaisesRegex(ValueError, "custom0.*does not exist in BoneHierarchy"):
            main._validate_custom_submeshes(model)

    def test_host_bone_already_owning_a_mesh_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(HostBoneId=2)]
        with self.assertRaisesRegex(ValueError, 'already owns submesh 1'):
            main._validate_custom_submeshes(model)

    def test_host_bone_claimed_twice_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [
            _validation_entry(CustomSubmeshId='custom0', HostBoneId=1),
            _validation_entry(CustomSubmeshId='custom1', HostBoneId=1),
        ]
        with self.assertRaisesRegex(ValueError, "also claimed by custom submesh 'custom0'"):
            main._validate_custom_submeshes(model)

    def test_malformed_template_source_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='nonsense')]
        with self.assertRaisesRegex(ValueError, 'rigid:<SurfaceId>'):
            main._validate_custom_submeshes(model)

    def test_missing_rigid_surface_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='rigid:sm1_ds99')]
        with self.assertRaisesRegex(ValueError, 'does not exist on a rigid submesh'):
            main._validate_custom_submeshes(model)

    def test_rigid_hand_role_surface_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='rigid:sm1_ds5')]
        with self.assertRaisesRegex(ValueError, 'hand/visibility-role'):
            main._validate_custom_submeshes(model)

    def test_derived_missing_surface_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='derived:sm0_ds99')]
        with self.assertRaisesRegex(ValueError, 'does not exist on submesh 0'):
            main._validate_custom_submeshes(model)

    def test_derived_non_drawing_surface_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='derived:sm0_ds3')]
        with self.assertRaisesRegex(ValueError, 'draws no primitives'):
            main._validate_custom_submeshes(model)

    def test_derived_hand_role_surface_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='derived:sm0_ds5')]
        with self.assertRaisesRegex(ValueError, 'RhSp'):
            main._validate_custom_submeshes(model)

    def test_derived_rejected_type6_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='derived:sm0_ds7')]
        with self.assertRaisesRegex(ValueError, '00000375'):
            main._validate_custom_submeshes(model)

    def test_derived_requires_skinned_submesh0(self):
        model = _validation_base_model()
        model['Submeshes'][0]['VertexBuffer']['VertexBufferCompCount'] = 3
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='derived:sm0_ds4')]
        with self.assertRaisesRegex(ValueError, 'skinned submesh 0'):
            main._validate_custom_submeshes(model)

    def test_unknown_builtin_name_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='builtin:nope')]
        with self.assertRaisesRegex(ValueError, 'unknown template'):
            main._validate_custom_submeshes(model)

    def test_tampered_builtin_hash_is_rejected(self):
        original = main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES['rigid_spec_v1']
        tampered = dict(original, States=original['States'][:-1] + ((7, '640064', 'RhSp'),))
        main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES['rigid_spec_v1'] = tampered
        try:
            model = _validation_base_model()
            model['CustomSubmeshes'] = [_validation_entry(TemplateSource='builtin:rigid_spec_v1')]
            with self.assertRaisesRegex(ValueError, 'recorded hash'):
                main._validate_custom_submeshes(model)
        finally:
            main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES['rigid_spec_v1'] = original

    def test_unverified_builtins_are_rejected(self):
        """Decision 9: a built-in stays refused until Phase 0 probe 7 confirms
        it in game, so an unverified capture cannot reach a DAT write."""
        unverified = [
            name for name, template in main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES.items()
            if not template['VerifiedInGame']
        ]
        self.assertTrue(unverified, 'expected at least one unverified built-in')
        for name in unverified:
            with self.subTest(name):
                model = _validation_base_model()
                model['CustomSubmeshes'] = [
                    _validation_entry(TemplateSource=f'builtin:{name}')
                ]
                with self.assertRaisesRegex(ValueError, 'not verified in game yet'):
                    main._validate_custom_submeshes(model)

    def test_builtin_layer_count_must_match_uv_channel_count(self):
        model = _validation_base_model()
        # Bind a layer-1 specular texture on submesh 0, so rigid_spec_v1 emits
        # two T1 records and Type 4 declares two channels -- which the
        # single-UV-channel entry would contradict.
        model['Submeshes'][0]['DisplayStates'].insert(1, _ds('sm0_ds0b', 1, '11002003'))
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='builtin:rigid_spec_v1')]
        with self.assertRaisesRegex(
            ValueError, r'binds 2 texture layer\(s\).*has 1 UV channel'
        ):
            main._validate_custom_submeshes(model)

    def test_one_layer_builtin_is_rejected_on_a_two_channel_submesh(self):
        model = _validation_base_model()
        original = main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES['rigid_shdw_v1']
        main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES['rigid_shdw_v1'] = dict(
            original, VerifiedInGame=True,
        )
        try:
            entry = _validation_entry(TemplateSource='builtin:rigid_shdw_v1')
            entry['UVChannels'] = entry['UVChannels'] + [
                dict(entry['UVChannels'][0], UVChannelIndex=1),
            ]
            model['CustomSubmeshes'] = [entry]
            with self.assertRaisesRegex(
                ValueError, r'binds 1 texture layer\(s\).*has 2 UV channel'
            ):
                main._validate_custom_submeshes(model)
        finally:
            main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES['rigid_shdw_v1'] = original

    def test_uv_channel_count_enforced_for_derived_and_builtin(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TemplateSource='derived:sm0_ds4', UVChannels=[])]
        with self.assertRaisesRegex(ValueError, 'UV channel count must be 1 or 2'):
            main._validate_custom_submeshes(model)

    def test_faces_count_mismatch_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(FacesCount=2)]
        with self.assertRaisesRegex(ValueError, 'does not match FacesCount'):
            main._validate_custom_submeshes(model)

    def test_face_index_out_of_range_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(
            FacesData=base64.b64encode(struct.pack('>3H', 0, 0, 5)).decode('ascii'),
        )]
        with self.assertRaisesRegex(ValueError, 'face index 5 is out of range'):
            main._validate_custom_submeshes(model)

    def test_malformed_vertex_buffer_length_is_rejected(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(
            VertexBufferData=base64.b64encode(bytes(5)).decode('ascii'),
        )]
        with self.assertRaisesRegex(ValueError, 'not a whole number of rigid position entries'):
            main._validate_custom_submeshes(model)

    def test_position_quantize_info_defaults_and_accepts_the_s16_range(self):
        for quantize_info in (None, 48, 55, 59):
            with self.subTest(quantize_info=quantize_info):
                model = _validation_base_model()
                entry = _validation_entry()
                if quantize_info is not None:
                    entry['VertexBufferQuantizeInfo'] = quantize_info
                model['CustomSubmeshes'] = [entry]
                main._validate_custom_submeshes(model)  # no raise

    def test_position_quantize_info_outside_the_s16_range_is_rejected(self):
        for quantize_info in (64, 60, 47, 0x40, 'x', True):
            with self.subTest(quantize_info=quantize_info):
                model = _validation_base_model()
                model['CustomSubmeshes'] = [_validation_entry(
                    VertexBufferQuantizeInfo=quantize_info,
                )]
                with self.assertRaisesRegex(ValueError, 'must be an s16 position format'):
                    main._validate_custom_submeshes(model)

    def test_uv_face_index_out_of_range_is_rejected(self):
        model = _validation_base_model()
        entry = _validation_entry()
        entry['UVChannels'][0]['UVFacesData'] = base64.b64encode(struct.pack('>3H', 0, 0, 9)).decode('ascii')
        model['CustomSubmeshes'] = [entry]
        with self.assertRaisesRegex(ValueError, 'UVChannels\\[0\\] face index 9 is out of range'):
            main._validate_custom_submeshes(model)

    def test_texture_assignment_requires_exactly_one_field(self):
        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TextureAssignment={})]
        with self.assertRaisesRegex(ValueError, 'exactly one of'):
            main._validate_custom_submeshes(model)

        model = _validation_base_model()
        model['CustomSubmeshes'] = [_validation_entry(TextureAssignment={
            'DonorTextureIndex': 0, 'AdditionalTextureFileName': 'x.png',
        })]
        with self.assertRaisesRegex(ValueError, 'exactly one of'):
            main._validate_custom_submeshes(model)

    def test_builtin_template_registry_matches_fixture_script_copy(self):
        # The fixture script reads the states and hash from this module; this
        # guards against it growing its own copy again.
        import build_template_source_fixture as tsf
        ours = main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES['rigid_spec_v1']
        theirs = tsf.BUILTIN_RIGID_SPEC_V1
        self.assertEqual(ours['States'], theirs['States'])
        self.assertEqual(ours['Sha256'], theirs['Sha256'])


class CustomSubmeshType3AndTextureHelperTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 2 step 3: the small bit-level helpers that
    encode/decode Type-1 texture bindings and Type-3 attribute layouts."""

    def test_texture_layer_round_trip(self):
        mode = '11110000'
        layer, texture_index = main._custom_submesh_texture_layer(mode)
        rebound = main._custom_submesh_with_texture_index(mode, 7)
        self.assertEqual(main._custom_submesh_texture_layer(rebound), (layer, 7))

    def test_type3_setting_round_trip(self):
        layout = [
            {'key': 'position', 'index_size': 1},
            {'key': 'lighting', 'index_size': 2},
            {'key': 'color0', 'index_size': 1},
            {'key': 'texture0', 'index_size': 1},
            {'key': 'texture1', 'index_size': 2},
        ]
        setting = main._custom_submesh_type3_setting(layout)
        self.assertEqual(main._custom_submesh_type3_descriptors(setting), layout)

    def test_type3_descriptors_reproduces_vanilla_values(self):
        # F9 survey values, decoded then re-encoded, must round-trip exactly.
        for hex_value in ('000028a8', '000028bc', '00003cbc', '00003ca8'):
            setting = int(hex_value, 16)
            layout = main._custom_submesh_type3_descriptors(setting)
            self.assertEqual(main._custom_submesh_type3_setting(layout), setting)

    def test_type3_descriptors_rejects_direct_attribute(self):
        with self.assertRaisesRegex(ValueError, 'direct'):
            main._custom_submesh_type3_descriptors(0b01 << main.drawlist._ATTR_BIT_SHIFT['position'])


class BuildCustomSubmeshAdditionalTextureIndexTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 4 step 2: _build_custom_submesh patches the
    new submesh's Type-1 texture binding with the final TEX index resolved
    from the caller's texture_index_by_file_name mapping -- there is no
    build-time placeholder to re-patch later."""

    def test_missing_mapping_entry_raises(self):
        model = _validation_base_model()
        cs = SimpleNamespace(
            template_source='builtin:rigid_spec_v1', custom_submesh_id='custom0',
            vertex_data=b'\x00' * 6, normal_data=None, color_data=None, uv_channels=[],
            texture_assignment=SimpleNamespace(
                donor_texture_index=None, additional_texture_file_name='new.png',
            ),
        )
        with self.assertRaisesRegex(ValueError, 'no resolved TEX index'):
            main._build_custom_submesh(
                model, cs, main._custom_submesh_rigid_surfaces(model), 1,
                texture_index_by_file_name={},
            )

    def test_resolved_mapping_patches_the_final_index(self):
        with synthetic_donor.donor_environment() as env:
            data = env.reload()
        model = data['SluggiesModel']
        model['UseHammerspace'] = True
        use_b64 = model.get('UseBase64', True)
        host_bone_id = _free_host_bones(model, 1)[0]
        cs_dict = _cube_custom_submesh('custom0', host_bone_id, 'builtin:rigid_spec_v1', use_b64)
        cs_dict['TextureAssignment'] = {'AdditionalTextureFileName': 'new.png'}
        model['CustomSubmeshes'] = [cs_dict]
        parsed = main.ParseSluggie(data)
        cs = parsed.custom_submeshes[0]
        sub = main._build_custom_submesh(
            model, cs, main._custom_submesh_rigid_surfaces(model), len(model['Submeshes']),
            texture_index_by_file_name={'new.png': 42},
        )
        layer0 = next(
            state.shader_mode for state in sub.draw_states
            if state.display_state_id == 1
            and main._custom_submesh_texture_layer(state.shader_mode)[0] == 0
        )
        self.assertEqual(main._custom_submesh_texture_layer(layer0), (0, 42))


class CustomSubmeshTemplateRecordsTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 2 step 3: resolving a CustomSubmesh's
    display-state records per TemplateSource kind."""

    def test_rigid_records_clone_the_whole_template_list_verbatim(self):
        model = _validation_base_model()
        rigid_surfaces = main._custom_submesh_rigid_surfaces(model)
        cs = SimpleNamespace(
            template_source='rigid:sm1_ds4', custom_submesh_id='custom0',
        )
        records, drawing_index, kind = main._resolve_custom_submesh_records(model, cs, rigid_surfaces)
        head_states = model['Submeshes'][1]['DisplayStates']
        self.assertEqual(kind, 'rigid')
        self.assertEqual(drawing_index, 4)
        self.assertEqual([r[0] for r in records], [int(s['DisplayStateId']) for s in head_states])
        self.assertEqual(records[4][2], 'Spec')

    def test_derived_records_use_canonical_order_and_placeholders(self):
        model = _validation_base_model()
        records, drawing_index, kind = main._resolve_custom_submesh_records(
            model, SimpleNamespace(template_source='derived:sm0_ds4', custom_submesh_id='custom0'), {},
        )
        self.assertEqual(kind, 'derived')
        self.assertEqual(drawing_index, len(records) - 1)
        self.assertEqual([r[0] for r in records], [1, 4, 3, 6, 7])
        self.assertEqual(records[1][2], 'fffffff0')  # T4: 1 UV channel (only layer 0 bound)
        self.assertEqual(records[2][2], '00000000')  # T3 placeholder, regenerated later
        self.assertEqual(records[3][2], '00000374')
        self.assertEqual(records[4][2], 'Spec')

    def test_builtin_records_rebind_to_host_submesh0_textures(self):
        model = _validation_base_model()
        records, drawing_index, kind = main._resolve_custom_submesh_records(
            model, SimpleNamespace(template_source='builtin:rigid_spec_v1', custom_submesh_id='custom0'), {},
        )
        self.assertEqual(kind, 'builtin')
        layer0 = next(r for r in records if r[0] == 1 and main._custom_submesh_texture_layer(r[2])[0] == 0)
        # sm0_ds0's ShaderMode is '11110000' -> texture index 0.
        self.assertEqual(main._custom_submesh_texture_layer(layer0[2]), (0, 0))
        type4 = next(r for r in records if r[0] == 4)
        self.assertEqual(type4[2], 'fffffff0')  # only layer 0 is bound on submesh 0 here

    def test_one_layer_builtin_keeps_type4_fffffff0_on_a_two_layer_host(self):
        """Decision 9: Type 4 follows the template's own layer count, so the
        1-layer `Shdw` form stays 1-layer even where `Spec` binds two."""
        model = _validation_base_model()
        model['Submeshes'][0]['DisplayStates'].insert(1, _ds('sm0_ds0b', 1, '11002003'))
        shdw, drawing_index, kind = main._resolve_custom_submesh_records(
            model,
            SimpleNamespace(template_source='builtin:rigid_shdw_v1', custom_submesh_id='custom0'),
            {},
        )
        self.assertEqual(kind, 'builtin')
        self.assertEqual(drawing_index, len(shdw) - 1)
        self.assertEqual([r[0] for r in shdw], [1, 4, 3, 6, 7])
        self.assertEqual(next(r for r in shdw if r[0] == 4)[2], 'fffffff0')
        self.assertEqual(shdw[-1][2], 'Shdw')
        # The same host binds both layers for the 2-layer built-in.
        spec, _index, _kind = main._resolve_custom_submesh_records(
            model,
            SimpleNamespace(template_source='builtin:rigid_spec_v1', custom_submesh_id='custom0'),
            {},
        )
        self.assertEqual([r[0] for r in spec], [1, 1, 4, 3, 6, 7])
        self.assertEqual(next(r for r in spec if r[0] == 4)[2], 'ffffff10')

    def test_builtin_layer_count_is_capped_by_host_bindings(self):
        model = _validation_base_model()
        # Only layer 0 is bound here, so even the 2-layer built-in emits one.
        self.assertEqual(main._custom_submesh_builtin_layer_count(model, 'rigid_spec_v1'), 1)
        self.assertEqual(main._custom_submesh_builtin_layer_count(model, 'rigid_shdw_v1'), 1)
        model['Submeshes'][0]['DisplayStates'].insert(1, _ds('sm0_ds0b', 1, '11002003'))
        self.assertEqual(main._custom_submesh_builtin_layer_count(model, 'rigid_spec_v1'), 2)
        self.assertEqual(main._custom_submesh_builtin_layer_count(model, 'rigid_shdw_v1'), 1)

    def test_every_builtin_rebinds_layer0_to_the_host_texture(self):
        model = _validation_base_model()
        model['Submeshes'][0]['DisplayStates'][0] = _ds('sm0_ds0', 1, '11110007', '000008')
        model['Submeshes'][0]['DisplayStates'].insert(1, _ds('sm0_ds0b', 1, '11002009'))
        for name, template in main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES.items():
            with self.subTest(name):
                records = main._custom_submesh_builtin_records(model, name)
                layer0 = next(
                    r for r in records
                    if r[0] == 1 and main._custom_submesh_texture_layer(r[2])[0] == 0
                )
                self.assertEqual(main._custom_submesh_texture_layer(layer0[2]), (0, 7))
                self.assertEqual(records[-1][2], template['ShaderMode'])
                self.assertEqual(
                    sum(1 for r in records if r[0] == 1), template['Layers'],
                )
                layer1 = [
                    r for r in records
                    if r[0] == 1 and main._custom_submesh_texture_layer(r[2])[0] == 1
                ]
                if template['Layers'] == 2:
                    self.assertEqual(main._custom_submesh_texture_layer(layer1[0][2]), (1, 9))
                else:
                    self.assertEqual(layer1, [])

    def test_builtin_template_names_offers_only_verified_templates(self):
        self.assertEqual(main.builtin_template_names(), ('rigid_spec_v1',))
        self.assertEqual(
            sorted(main.builtin_template_names(verified_only=False)),
            sorted(main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES),
        )

    def test_registry_entries_are_self_consistent(self):
        """Every entry's stored hash, layer count and mode agree with its
        records, so a hand-edited capture cannot pass unnoticed."""
        self.assertEqual(
            sorted(main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES),
            ['rigid_ghsp_v1', 'rigid_lhsp_v1', 'rigid_rhsp_v1', 'rigid_shdw_v1',
             'rigid_spec_v1'],
        )
        for name, template in main._CUSTOM_SUBMESH_BUILTIN_TEMPLATES.items():
            with self.subTest(name):
                self.assertEqual(
                    main._custom_submesh_state_records_sha256(template['States']),
                    template['Sha256'],
                )
                states = template['States']
                self.assertEqual(
                    template['Layers'], sum(1 for s, _p, _m in states if s == 1),
                )
                self.assertEqual([s for s, _p, _m in states][-4:], [4, 3, 6, 7])
                self.assertEqual(states[-1][0], 7)
                self.assertEqual(states[-1][2], template['ShaderMode'])
                self.assertEqual(
                    next(m for s, _p, m in states if s == 4),
                    main._CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT[template['Layers']],
                )

    def test_patch_layer0_texture_only_changes_layer0(self):
        records = [[1, b'\x00\x00\x00', '11110000'], [1, b'\x00\x00\x00', '11002003'], [7, b'\x00\x00\x00', 'Spec']]
        main._custom_submesh_patch_layer0_texture(records, 2, 9)
        self.assertEqual(main._custom_submesh_texture_layer(records[0][2]), (0, 9))
        self.assertEqual(records[1][2], '11002003')  # layer 1 (specular) untouched


class BuildRigidSubmeshBlobTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 2 step 1: the self-contained single-submesh
    GPL blob writer a new custom submesh is serialized with."""

    def _submesh(self, **overrides):
        base = dict(
            submesh_index=3,
            mesh_name='CustomSubmesh_0',
            faces_count=1,
            faces_data=struct.pack('>3H', 0, 1, 2),
            face_texture_indices=b'',
            vertex_data=struct.pack('>9h', 0, 0, 0, 1, 0, 0, 0, 1, 0),
            vertex_comp_count=3,
            vertex_quantize_info=59,
            uv_channels=[],
            color_channels=[],
            draw_states=[main.DrawState(
                display_state_id=7, display_state_pad_bytes=b'\x64\x00\x64',
                prim_list_data=b'\x90\x00\x03' + b'\x00' * 29, active_descriptors=[],
                prim_list_ptr_field_offset=0, prim_list_size_field_offset=0,
                prim_list_absolute_offset=0, prim_list_length=32,
                shader_mode_field_offset=0, shader_mode='Spec', source_state_offset=0,
            )],
            position_data_ptr_field_offset=0,
            vertex_count_field_offset=0,
            normal_buffer=None,
            source_layout_offset=0,
            source_position_data_offset=0,
            preserve_source_layout=False,
        )
        base.update(overrides)
        return main.Submesh(**base)

    def test_blob_layout_matches_dolayout_conventions(self):
        sub = self._submesh()
        blob, name_off = main._build_rigid_submesh_blob(sub)

        pos_off, col_off, uv_off, nor_off, dsp_off = struct.unpack_from('>5I', blob, 0x00)
        m_uv = blob[0x14]
        self.assertEqual((pos_off, col_off, uv_off, nor_off), (0x18, 0x20, 0x28, 0))
        self.assertEqual(m_uv, 0)

        pos_ptr, pos_count, pos_quant, pos_cc = struct.unpack_from('>IHBB', blob, pos_off)
        self.assertEqual(pos_count, 3)
        self.assertEqual((pos_quant, pos_cc), (59, 3))
        self.assertEqual(
            struct.unpack_from('>9h', blob, pos_ptr), (0, 0, 0, 1, 0, 0, 0, 1, 0),
        )

        n_ds = struct.unpack_from('>H', blob, dsp_off + 0x08)[0]
        self.assertEqual(n_ds, 1)
        ds_off = struct.unpack_from('>I', blob, dsp_off + 0x04)[0]
        state_id = blob[ds_off]
        pl_ptr, pl_len = struct.unpack_from('>II', blob, ds_off + 0x08)
        self.assertEqual(state_id, 7)
        self.assertEqual(pl_len, 32)
        self.assertEqual(pl_ptr % 32, 0)  # primitive lists are always 32-byte aligned
        self.assertEqual(blob[pl_ptr:pl_ptr + 3], b'\x90\x00\x03')

        self.assertEqual(blob[name_off:name_off + len('CustomSubmesh_0')], b'CustomSubmesh_0')
        self.assertEqual(blob[name_off + len('CustomSubmesh_0')], 0)  # NUL terminator

    def test_normal_buffer_absent_leaves_dolayout_pointer_zero(self):
        sub = self._submesh()
        blob, _ = main._build_rigid_submesh_blob(sub)
        nor_off = struct.unpack_from('>I', blob, 0x0c)[0]
        self.assertEqual(nor_off, 0)

    def test_normal_buffer_present_is_wired_into_dolayout(self):
        sub = self._submesh(normal_buffer=main.NormalBuffer(
            normal_data_ptr_field_offset=0, normal_count_field_offset=0,
            normal_buffer_offset=0, normal_buffer_length=6, comp_count=3,
            quantize_info=62, ambient_pct=0.0,
            normal_data=struct.pack('>3h', 0, 16384, 0), source_header_offset=0,
        ))
        blob, _ = main._build_rigid_submesh_blob(sub)
        nor_off = struct.unpack_from('>I', blob, 0x0c)[0]
        self.assertNotEqual(nor_off, 0)
        nor_ptr, nor_count, nor_quant, nor_cc = struct.unpack_from('>IHBB', blob, nor_off)
        self.assertEqual((nor_count, nor_quant, nor_cc), (1, 62, 3))
        self.assertEqual(struct.unpack_from('>3h', blob, nor_ptr), (0, 16384, 0))


class PatchGPLAppendSubmeshTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 2: appends a new GEO descriptor + blob to a
    cloned GPL section, relocating existing blobs as a single unit."""

    def _build_donor_gpl(self, blob_start: int, blob_bytes: bytes, name_off: int, user_data: bytes):
        """A minimal one-submesh GPL whose blob starts immediately after the
        descriptor table -- deliberately NOT 32-aligned, matching real donor
        data (e.g. real Mario's blob 0 starts at GPL+0x2C)."""
        desc_ptr = 0x14
        user_data_off = blob_start + len(blob_bytes)
        gpl = bytearray(user_data_off + len(user_data))
        struct.pack_into('>5I', gpl, 0x00, main.GPL_MAGIC if hasattr(main, 'GPL_MAGIC') else 0x00B749E0,
                          len(user_data), user_data_off, 1, desc_ptr)
        struct.pack_into('>II', gpl, desc_ptr, blob_start, blob_start + name_off)
        gpl[blob_start:blob_start + len(blob_bytes)] = blob_bytes
        gpl[user_data_off:] = user_data
        return bytes(gpl), desc_ptr, blob_start, user_data_off

    def test_append_relocates_existing_blob_preserving_mod32_residue(self):
        blob_start = 0x1C  # 28, not a multiple of 32 -- the real-world shape
        blob_bytes = bytes(range(40))
        name_off = 10
        user_data = b'USERDATA' * 5
        gpl_bytes, desc_ptr, old_blob_start, old_user_data_off = self._build_donor_gpl(
            blob_start, blob_bytes, name_off, user_data,
        )

        new_sub = main.Submesh(
            submesh_index=1, mesh_name='CustomSubmesh_0', faces_count=1,
            faces_data=struct.pack('>3H', 0, 1, 2), face_texture_indices=b'',
            vertex_data=struct.pack('>9h', 0, 0, 0, 1, 0, 0, 0, 1, 0),
            vertex_comp_count=3, vertex_quantize_info=59,
            uv_channels=[], color_channels=[], draw_states=[], position_data_ptr_field_offset=0,
            vertex_count_field_offset=0, normal_buffer=None, source_layout_offset=0,
            source_position_data_offset=0, preserve_source_layout=False,
        )
        parsed = SimpleNamespace(custom_submeshes=[SimpleNamespace(
            custom_submesh_id='custom0', host_bone_id=5, template_source='builtin:rigid_spec_v1',
        )])
        with mock.patch.object(main, '_build_custom_submesh', return_value=new_sub):
            patched = main.PatchGPLAppendSubmesh(gpl_bytes, {'Submeshes': []}, parsed)

        magic, ud_len, ud_ptr, count, out_desc_ptr = struct.unpack_from('>5I', patched, 0x00)
        self.assertEqual(count, 2)
        self.assertEqual(out_desc_ptr, desc_ptr)

        old_dolayout_ptr, old_name_ptr = struct.unpack_from('>II', patched, desc_ptr)
        shift = old_dolayout_ptr - old_blob_start
        self.assertEqual(shift % 32, 0, 'relocation must preserve the mod-32 residue')
        self.assertEqual(old_name_ptr - old_dolayout_ptr, name_off)
        self.assertEqual(
            patched[old_dolayout_ptr:old_dolayout_ptr + len(blob_bytes)], blob_bytes,
            'the donor blob must be relocated byte-for-byte unchanged',
        )

        self.assertEqual(ud_len, len(user_data))
        self.assertEqual(patched[ud_ptr:ud_ptr + len(user_data)], user_data)

        new_dolayout_ptr, new_name_ptr = struct.unpack_from('>II', patched, desc_ptr + 8)
        self.assertEqual(new_dolayout_ptr % 32, 0, 'a freshly appended blob is always 32-aligned')
        rebuilt_blob, rebuilt_name_off = main._build_rigid_submesh_blob(new_sub)
        self.assertEqual(
            patched[new_dolayout_ptr:new_dolayout_ptr + len(rebuilt_blob)], rebuilt_blob,
        )
        self.assertEqual(new_name_ptr - new_dolayout_ptr, rebuilt_name_off)

    def test_no_custom_submeshes_returns_input_unchanged(self):
        gpl_bytes, *_ = self._build_donor_gpl(0x1C, bytes(range(40)), 10, b'UD')
        result = main.PatchGPLAppendSubmesh(gpl_bytes, {'Submeshes': []}, SimpleNamespace(custom_submeshes=[]))
        self.assertEqual(result, gpl_bytes)


def _cube_custom_submesh(cs_id: str, host_bone_id: int, template_source: str, use_b64: bool) -> dict:
    """A structurally valid 12-triangle cube CustomSubmeshes entry with a
    normal, one color entry and two UV channels, so it fits any of the three
    template sources' attribute requirements."""
    def s16(values):
        return struct.pack(f'>{len(values)}h', *values)

    def encode(data: bytes):
        return base64.b64encode(data).decode('ascii') if use_b64 else list(data)

    extent = 200
    corners = []
    for i in range(8):
        corners += [extent if i & 4 else -extent, extent if i & 2 else -extent, extent if i & 1 else -extent]
    cube_faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    face_indices = []
    for a, b, c, d in cube_faces:
        face_indices += [a, b, c, a, c, d]
    faces_count = len(face_indices) // 3
    faces_data = struct.pack(f'>{len(face_indices)}H', *face_indices)

    normal_dirs = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    normal_data = s16([int(c * 16000) for n in normal_dirs for c in n])
    normal_faces = struct.pack(f'>{faces_count * 3}H', *(face_i for face_i in range(6) for _ in range(6)))

    uv_pairs = []
    for i in range(8):
        uv_pairs += [100 if i & 4 else 0, 100 if i & 2 else 0]
    uv_bytes = s16(uv_pairs)

    return {
        'CustomSubmeshId': cs_id,
        'MeshName': cs_id,
        'HostBoneId': host_bone_id,
        'TemplateSource': template_source,
        'VertexBufferData': encode(s16(corners)),
        'NormalBufferData': encode(normal_data),
        'NormalFacesData': encode(normal_faces),
        'ColorChannelData': encode(bytes([255, 255, 255, 255])),
        'ColorFacesData': encode(struct.pack(f'>{faces_count * 3}H', *([0] * (faces_count * 3)))),
        'FacesCount': faces_count,
        'FacesData': encode(faces_data),
        'UVChannels': [
            {
                'UVChannelIndex': i,
                'UVChannelData': encode(uv_bytes),
                'UVFacesData': encode(faces_data),
            }
            for i in range(2)
        ],
        'TextureAssignment': {'DonorTextureIndex': 0},
    }


def _free_host_bones(model: dict, count: int) -> list:
    skn_used = set()
    skin_data = model.get('SkinData') or {}
    for entry in skin_data.get('SK1s', []) + skin_data.get('SK2s', []):
        skn_used.add(int(entry.get('BoneId', -1)))
    candidates = [
        int(bone['BoneId']) for bone in model['BoneHierarchy']
        if main._bone_geo_id_raw(bone) == 0xFFFF
        and int(bone['BoneId']) not in skn_used
        and bone.get('ParentBoneId') is not None
    ]
    return candidates[:count]


def _strip_edited_fields(node) -> None:
    """Remove every ``*Edited`` key from a parsed .sluggie tree, in place."""
    if isinstance(node, dict):
        for key in [k for k in node if k.endswith('Edited')]:
            del node[key]
        for value in node.values():
            _strip_edited_fields(value)
    elif isinstance(node, list):
        for value in node:
            _strip_edited_fields(value)


class PatchGPLAppendSubmeshSyntheticDonorTests(unittest.TestCase):
    """End-to-end smoke test over the synthetic donor: appends a cube
    CustomSubmesh through the full BuildModelBlock pipeline and validates the
    assembled block, for each PLAN_AddSubmesh.md template source. GeoId
    patching is Phase 3's job, so the new submesh is valid but unowned here --
    BlockValidator does not require an owner."""

    def setUp(self):
        self.env = self.enterContext(synthetic_donor.donor_environment())

    def _load(self):
        data = self.env.reload()
        model = data['SluggiesModel']
        model['UseHammerspace'] = True
        return data, model

    def _build(self, template_source: str, host_index: int = 0):
        data, model = self._load()
        use_b64 = model.get('UseBase64', True)
        host_bone_id = _free_host_bones(model, host_index + 1)[host_index]
        model['CustomSubmeshes'] = [
            _cube_custom_submesh('custom0', host_bone_id, template_source, use_b64)
        ]
        modes = main.SectionModes(gpl='build', act='clone', tex='clone', skn='clone', trailing='clone')
        return main.BuildModelBlock(data, modes, sluggie_path=self.env.sluggie_path), model

    def _rigid_surface_id(self, model: dict) -> str:
        for submesh in model['Submeshes']:
            if int(submesh['VertexBuffer']['VertexBufferCompCount']) != 3:
                continue
            for state in submesh.get('DisplayStates', []):
                if state.get('SurfaceId') and int(state.get('PrimListLength') or 0) > 0:
                    return state['SurfaceId']
        raise AssertionError('no rigid drawing surface found in the real Mario export')

    def _derived_surface_id(self, model: dict) -> str:
        submesh0 = model['Submeshes'][0]
        for state in submesh0.get('DisplayStates', []):
            if state.get('SurfaceId') and int(state.get('PrimListLength') or 0) > 0:
                return state['SurfaceId']
        raise AssertionError('no derived-eligible surface found in the real Mario export')

    def test_builtin_template_produces_a_valid_block_with_one_more_submesh(self):
        data, model = self._load()
        donor_count = len(model['Submeshes'])
        build, _ = self._build('builtin:rigid_spec_v1', host_index=0)
        self.assertTrue(build.validation_report['valid'], build.validation_report.get('errors'))
        self.assertEqual(
            len(build.validation_report['validator_facts']['gpl_submesh_layout']), donor_count + 1,
        )

    def test_rigid_template_produces_a_valid_block(self):
        _, model = self._load()
        surface_id = self._rigid_surface_id(model)
        build, _ = self._build(f'rigid:{surface_id}', host_index=1)
        self.assertTrue(build.validation_report['valid'], build.validation_report.get('errors'))

    def test_derived_template_produces_a_valid_block(self):
        _, model = self._load()
        surface_id = self._derived_surface_id(model)
        build, _ = self._build(f'derived:{surface_id}', host_index=2)
        self.assertTrue(build.validation_report['valid'], build.validation_report.get('errors'))

    def test_donor_submesh_blobs_are_byte_for_byte_unchanged(self):
        data, model = self._load()
        offset = model['ModelOffset']
        offset = int(offset, 16) if isinstance(offset, str) else int(offset)
        length = int(model.get('ModelLength', 0))
        donor_gpl = main.CloneGPL(offset, length)
        donor_count, donor_desc_ptr = struct.unpack_from('>I', donor_gpl, 0x0c)[0], struct.unpack_from('>I', donor_gpl, 0x10)[0]
        donor_pointers = sorted(
            struct.unpack_from('>II', donor_gpl, donor_desc_ptr + i * 8)[0] for i in range(donor_count)
        )
        donor_user_data_ptr = struct.unpack_from('>I', donor_gpl, 0x08)[0]
        donor_bounds = donor_pointers + [donor_user_data_ptr or len(donor_gpl)]
        donor_blobs = [
            donor_gpl[start:donor_bounds[index + 1]] for index, start in enumerate(donor_pointers)
        ]

        build, _ = self._build('builtin:rigid_spec_v1', host_index=0)
        prefix = int(build.validation_report.get('container_prefix_size', 0))
        inner = int(build.validation_report.get('inner_assembled_size', len(build.block) - prefix))
        block = build.block[prefix:prefix + inner]
        gpl_off = struct.unpack_from('>I', block, 0x04)[0]
        new_gpl = block[gpl_off:]
        new_pointers = sorted(
            struct.unpack_from('>II', new_gpl, donor_desc_ptr + i * 8)[0] for i in range(donor_count)
        )
        new_user_data_ptr = struct.unpack_from('>I', new_gpl, 0x08)[0]
        new_bounds = new_pointers + [new_user_data_ptr or len(new_gpl)]
        for index, start in enumerate(new_pointers):
            content = new_gpl[start:new_bounds[index + 1]]
            donor_content = donor_blobs[index]
            compare_len = min(len(content), len(donor_content))
            self.assertEqual(
                content[:compare_len], donor_content[:compare_len],
                f'donor submesh {index} content changed',
            )


if __name__ == '__main__':
    unittest.main()