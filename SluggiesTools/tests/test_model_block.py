"""The shared model block reader (``Hammerspace/ModelBlock.py``) and the way
the modules built on it turn its one error into their own behaviour."""
import pathlib
import struct
import sys
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
TESTS_DIR = TOOLS_DIR / 'tests'
for _path in (TOOLS_DIR, HAMMERSPACE_DIR, TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import BlockValidator  # noqa: E402
import LodPartnerGuard  # noqa: E402
import LodTextureSync  # noqa: E402
import ModelBlock as mb  # noqa: E402
import synthetic_donor as donor  # noqa: E402
import UntangledTextures  # noqa: E402
from tpl import TEXDescriptor  # noqa: E402


def donor_block() -> bytes:
    _data, dat, length = donor.build_donor()
    return bytes(dat[donor.MODEL_OFFSET:donor.MODEL_OFFSET + length])


BLOCK = donor_block()
#: The donor block's SKAcc record leaves its bone id field at zero.
SKN_ACC_BLOCK_BONE = 0


def _name(model: mb.ModelBlock, offset: int) -> bytes:
    return model.raw(offset, model.find_nul(offset) - offset)


class RawAccessTests(unittest.TestCase):
    def test_checked_reads_are_big_endian(self):
        model = mb.ModelBlock(bytes([0x01, 0x02, 0x03, 0x04, 0x05]))
        self.assertEqual(model.u8(4), 0x05)
        self.assertEqual(model.u16(1), 0x0203)
        self.assertEqual(model.u32(0), 0x01020304)
        self.assertEqual(model.uint(0, 3), 0x010203)
        self.assertEqual(model.unpack('>HB', 2), (0x0304, 0x05))

    def test_a_read_past_the_end_raises_the_one_error(self):
        model = mb.ModelBlock(bytes(5))
        for read in (lambda: model.u8(5), lambda: model.u16(4), lambda: model.u32(2),
                     lambda: model.u32(-1), lambda: model.unpack('>I', 2), lambda: model.pointer('GPL')):
            with self.assertRaises(mb.ModelBlockError):
                read()

    def test_the_error_is_what_the_hand_written_readers_raised(self):
        self.assertTrue(issubclass(mb.ModelBlockError, struct.error))
        self.assertTrue(issubclass(mb.ModelBlockError, IndexError))

    def test_has(self):
        model = mb.ModelBlock(bytes(8))
        self.assertTrue(model.has(0, 8))
        self.assertTrue(model.has(8, 0))
        self.assertFalse(model.has(1, 8))
        self.assertFalse(model.has(-1, 2))

    def test_raw_and_find_nul_are_unchecked(self):
        model = mb.ModelBlock(b'ab\x00cd')
        self.assertEqual(model.raw(3, 10), b'cd')
        self.assertEqual(model.raw(9, 4), b'')
        self.assertEqual(model.find_nul(0), 2)
        self.assertEqual(model.find_nul(3), -1)
        self.assertEqual(model.find_nul(99), -1)

    def test_a_view_reads_nothing_until_a_field_is_used(self):
        view = mb.TexDescriptor(mb.ModelBlock(bytes(4)), 0x1000)
        with self.assertRaises(mb.ModelBlockError):
            view.width


class DonorBlockTests(unittest.TestCase):
    def setUp(self):
        self.model = mb.ModelBlock(BLOCK)

    def test_header_pointers(self):
        pointers = self.model.pointers()
        self.assertEqual(list(pointers), ['GPL', 'ACT', 'TEX', 'SKN', 'ptr6', 'ptr7', 'ptr8'])
        self.assertEqual(pointers['GPL'], 0x20)
        self.assertEqual((pointers['ptr6'], pointers['ptr7'], pointers['ptr8']), (0, 0, 0))
        self.assertEqual(self.model.pointer('TEX'), pointers['TEX'])
        self.assertEqual(self.model.tex().offset, pointers['TEX'])

    def test_gpl_submeshes_and_names(self):
        gpl = self.model.gpl()
        self.assertEqual(gpl.magic, mb.GPL_MAGIC)
        self.assertEqual(gpl.submesh_count, 2)
        descriptors = list(gpl.descriptors())
        self.assertEqual([_name(self.model, gpl.offset + d.name_ptr) for d in descriptors], [b'body', b'head'])

        skinned, rigid = (gpl.layout(d.layout_ptr) for d in descriptors)
        position = skinned.array_header(skinned.position_header_ptr)
        self.assertEqual((position.count, position.quantize_info, position.comp_count),
                         (donor.SKINNED_VERTEX_COUNT, donor.SKINNED_QUANTIZE_INFO, donor.SKINNED_COMP_COUNT))
        position = rigid.array_header(rigid.position_header_ptr)
        self.assertEqual((position.count, position.quantize_info, position.comp_count),
                         (donor.RIGID_VERTEX_COUNT, donor.RIGID_QUANTIZE_INFO, donor.RIGID_COMP_COUNT))
        self.assertEqual(skinned.uv_count, donor.UV_CHANNEL_COUNT)
        uv = skinned.array_header(skinned.uv_header_ptr + skinned.UV_HEADER_STRIDE)
        self.assertEqual((uv.count, uv.comp_count), (donor.SKINNED_VERTEX_COUNT, donor.UV_COMP_COUNT))
        self.assertEqual(_name(self.model, skinned.offset + uv.palette_name_ptr), donor.PALETTE_NAME.encode())

    def test_display_states(self):
        gpl = self.model.gpl()
        for descriptor in gpl.descriptors():
            layout = gpl.layout(descriptor.layout_ptr)
            states = list(layout.display_states())
            self.assertEqual(layout.display_header().state_count, len(donor.DISPLAY_STATE_TEMPLATE))
            self.assertEqual([(s.id, s.params.hex()) for s in states],
                             [(state_id, params) for state_id, params, _mode in donor.DISPLAY_STATE_TEMPLATE])
            self.assertEqual(states[0].setting, 0x11110000)
            self.assertEqual(states[1].setting & mb.TEXTURE_INDEX_MASK, 1)
            self.assertEqual(states[1].setting_offset, states[1].offset + 4)
            surface = states[donor.SURFACE_STATE_INDEX]
            self.assertEqual(surface.primitive_length, 32)
            self.assertEqual(self.model.u8(layout.offset + surface.primitive_ptr), 0x98)
            self.assertEqual(states[1].offset - states[0].offset, mb.DisplayState.SIZE)

    def test_tex_descriptors(self):
        tex = self.model.tex()
        self.assertEqual((tex.count, tex.clut_count), (donor.TEXTURE_COUNT, 0))
        descriptors = list(tex.descriptors())
        self.assertEqual(len(descriptors), donor.TEXTURE_COUNT)
        for index, descriptor in enumerate(descriptors):
            self.assertEqual(descriptor.image_ptr, donor._tex_image_pointer(index))
            self.assertEqual((descriptor.width, descriptor.height, descriptor.format),
                             (donor.TEXTURE_WIDTH, donor.TEXTURE_HEIGHT, donor.TEXTURE_FORMAT))
            self.assertEqual((descriptor.palette_ptr, descriptor.palette_entries), (0, 0))
            self.assertEqual(self.model.raw(tex.offset + descriptor.image_ptr, donor.TEXTURE_PAYLOAD_LENGTH),
                             bytes([0xAA] * donor.TEXTURE_PAYLOAD_LENGTH))

    def test_act_bones(self):
        act = self.model.act()
        self.assertEqual(act.bone_count, len(donor.BONE_PARENTS))
        self.assertEqual(act.root_ptr, act.HEADER_SIZE)
        self.assertEqual(_name(self.model, act.offset + act.geo_name_ptr), b'synthetic.gpl')
        for index in range(act.bone_count):
            bone = act.bone(index)
            self.assertEqual(bone.bone_id, index)
            self.assertEqual(bone.geo_id, donor.RIGID_OWNER_SUBMESH if index == donor.RIGID_OWNER_BONE else 0xFFFF)
            parent = donor.BONE_PARENTS[index]
            self.assertEqual(bone.parent_ptr, 0 if parent is None else act.HEADER_SIZE + parent * mb.Bone.SIZE)
        # a tree link is the linked record's start
        spine = act.bone_at(act.bone(0).first_child_ptr)
        self.assertEqual(spine.bone_id, 1)
        self.assertEqual(act.bone_at(spine.next_ptr).bone_id, 8)
        self.assertEqual(act.bone_at(act.bone(8).previous_ptr).bone_id, 1)

    def test_skn_records(self):
        skn = self.model.skn()
        self.assertEqual((skn.sk1_count, skn.sk2_count, skn.acc_count), (1, 0, 1))
        self.assertEqual(skn.quantize_info, donor.SKINNED_QUANTIZE_INFO)
        self.assertEqual(skn.sk1_ptr, skn.HEADER_SIZE)
        self.assertEqual((skn.mem_clear_ptr, skn.mem_clear_size),
                         (donor.SK_ACC_VERTEX * donor.SKINNED_VERTEX_STRIDE, 32))
        self.assertEqual(skn.flush_count, 1)
        self.assertEqual(self.model.u16(skn.offset + skn.flush_ptr), donor.SK_ACC_VERTEX)

        sk1s, sk2s, accs = skn.entries()
        self.assertEqual((len(sk1s), len(sk2s), len(accs)), (1, 0, 1))
        sk1, acc = sk1s[0], accs[0]
        self.assertEqual((sk1.bone_id, sk1.vertex_count, sk1.vertex_offset, sk1.vertex_array_ptr),
                         (donor.SKINNED_BONE, donor.SK1_VERTEX_COUNT, 0, 0))
        self.assertEqual((acc.bone_id, acc.vertex_count), (SKN_ACC_BLOCK_BONE, 1))
        self.assertEqual(self.model.u16(skn.offset + acc.destination_index_ptr), donor.SK_ACC_VERTEX)
        self.assertEqual(self.model.u8(skn.offset + acc.weight_ptr), 0xFF)
        self.assertEqual(skn.bone_id_offsets(), [sk1.offset + 0x38, acc.offset + 0x40])
        self.assertEqual(skn.sk1(skn.sk1_ptr, 0).offset, sk1.offset)

    def test_sk2_record_fields(self):
        record = bytearray(mb.Sk2.SIZE)
        struct.pack_into('>IIIHHHB', record, 0x60, 0x100, 0x200, 0x40, 7, 9, 5, 12)
        sk2 = mb.Sk2(mb.ModelBlock(bytes(record)), 0)
        self.assertEqual((sk2.source_ptr, sk2.weight_ptr, sk2.vertex_array_ptr), (0x100, 0x200, 0x40))
        self.assertEqual((sk2.bone_id_1, sk2.bone_id_2, sk2.vertex_count, sk2.vertex_offset), (7, 9, 5, 12))
        self.assertEqual(mb.Sk2.BONE_ID_FIELDS, (0x6C, 0x6E))

    def test_absent_sections(self):
        block = bytearray(BLOCK)
        struct.pack_into('>4I', block, 0x04, 0, 0, 0, 0)
        model = mb.ModelBlock(bytes(block))
        self.assertIsNone(model.gpl())
        self.assertIsNone(model.act())
        self.assertIsNone(model.tex())
        self.assertIsNone(model.skn())

    def test_a_model_inside_a_larger_buffer(self):
        prefix = 0x60
        model = mb.ModelBlock(bytes(prefix) + BLOCK, prefix)
        plain = mb.ModelBlock(BLOCK)
        self.assertEqual(model.pointers(), plain.pointers())
        self.assertEqual(model.tex().offset, plain.tex().offset + prefix)
        self.assertEqual([d.width for d in model.tex().descriptors()], [donor.TEXTURE_WIDTH] * donor.TEXTURE_COUNT)
        self.assertEqual(model.gpl().magic, mb.GPL_MAGIC)

    def test_every_cut_raises_only_the_one_error(self):
        def read_everything(data):
            model = mb.ModelBlock(data)
            gpl = model.gpl()
            for descriptor in gpl.descriptors():
                layout = gpl.layout(descriptor.layout_ptr)
                descriptor.name_ptr
                for pointer in (layout.position_header_ptr, layout.color_header_ptr, layout.normal_header_ptr):
                    header = layout.array_header(pointer)
                    header.data_ptr, header.count, header.quantize_info, header.comp_count
                for state in layout.display_states():
                    state.id, state.setting, state.primitive_ptr, state.primitive_length
            for descriptor in model.tex().descriptors():
                descriptor.image_ptr, descriptor.format, descriptor.palette_entries
            act = model.act()
            for index in range(act.bone_count):
                act.bone(index).bone_id
            skn = model.skn()
            [model.u16(at) for at in skn.bone_id_offsets()]

        read_everything(BLOCK)
        pointers = mb.ModelBlock(BLOCK).pointers()
        for cut in sorted({0, 3, 0x1F, 0x20, 0x33, pointers['ACT'] + 3, pointers['TEX'] + 1,
                           pointers['TEX'] + 0x1B, pointers['SKN'] + 5, pointers['SKN'] + 0x30}):
            with self.subTest(cut=cut), self.assertRaises(mb.ModelBlockError):
                read_everything(BLOCK[:cut])


class TexturePayloadLenTests(unittest.TestCase):
    SIZES = (0, 1, 2, 3, 4, 5, 7, 8, 9, 16, 31, 32, 33, 64, 213, 256, 1023, 1024, 0xFFFF)

    def test_matches_the_export_for_every_format_it_knows(self):
        stub = TEXDescriptor.__new__(TEXDescriptor)
        for fmt in range(256):
            for width in self.SIZES:
                for height in self.SIZES:
                    stub.format, stub.width, stub.height = fmt, width, height
                    try:
                        expected = stub._data_length()
                    except KeyError:
                        expected = None
                    self.assertEqual(mb.texture_payload_len(fmt, width, height), expected, (fmt, width, height))

    def test_known_formats(self):
        self.assertEqual(sorted(mb.TEXTURE_BITS_PER_PIXEL), [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 14])
        self.assertEqual(mb.texture_payload_len(14, 8, 8), 32)      # CMPR
        self.assertEqual(mb.texture_payload_len(6, 4, 4), 64)       # RGBA8
        self.assertEqual(mb.texture_payload_len(14, 213, 64), 6816)  # not rounded up to whole blocks
        self.assertIsNone(mb.texture_payload_len(7, 4, 4))


class ErrorTranslationTests(unittest.TestCase):
    """Each module keeps its own answer to a block it cannot read."""

    def test_texture_binds_of_a_cut_block_are_empty(self):
        binds = LodTextureSync.texture_binds(BLOCK)
        self.assertEqual(len(binds), 4)
        gpl = mb.ModelBlock(BLOCK).pointer('GPL')
        for cut in (0, 6, gpl + 2, gpl + 0x12, binds[-1].setting_offset + 2):
            self.assertEqual(LodTextureSync.texture_binds(BLOCK[:cut]), [], cut)

    def test_texture_bind_fields(self):
        bind = LodTextureSync.texture_binds(BLOCK)[1]
        self.assertEqual((bind.submesh, bind.mesh_name, bind.display_state, bind.params, bind.layer, bind.texture),
                         (0, 'body', 1, bytes(3), 1, 1))
        self.assertEqual(mb.ModelBlock(BLOCK).u32(bind.setting_offset), 0x11002001)

    def test_texture_count_of_a_cut_block_is_none(self):
        self.assertEqual(LodTextureSync.texture_count(BLOCK), donor.TEXTURE_COUNT)
        tex = mb.ModelBlock(BLOCK).pointer('TEX')
        for cut in (0, 0x0E, tex, tex + 1):
            self.assertIsNone(LodTextureSync.texture_count(BLOCK[:cut]), cut)

    def test_act_summary_of_a_cut_block_is_none(self):
        summary = LodPartnerGuard.act_summary(BLOCK)
        self.assertEqual((summary.geo_name, summary.bone_count), ('synthetic.gpl', len(donor.BONE_PARENTS)))
        self.assertEqual(summary.drawn_bones, {donor.RIGID_OWNER_BONE: donor.RIGID_OWNER_SUBMESH})
        self.assertEqual(summary.parents, donor.BONE_PARENTS)
        self.assertEqual(summary.skin_bones, frozenset({donor.SKINNED_BONE, SKN_ACC_BLOCK_BONE}))
        act = mb.ModelBlock(BLOCK).pointer('ACT')
        for cut in (0, 0x1F, act + 0x10, act + 0x20 + 3 * mb.Bone.SIZE):
            self.assertIsNone(LodPartnerGuard.act_summary(BLOCK[:cut]), cut)

    def test_skin_bones_skip_a_record_cut_off_and_keep_the_others(self):
        skn = mb.ModelBlock(BLOCK).skn()
        _sk1s, _sk2s, accs = skn.entries()
        cut = BLOCK[:accs[0].offset + 0x40]     # the SKAcc's bone id is gone, the SK1's is not
        self.assertEqual(LodPartnerGuard._skin_bones(cut), frozenset({donor.SKINNED_BONE}))
        self.assertEqual(LodPartnerGuard._skin_bones(BLOCK[:skn.offset + 0x23]), frozenset())
        self.assertEqual(LodPartnerGuard._skin_bones(BLOCK[:0x12]), frozenset())

    def test_untangled_textures_let_the_error_through(self):
        self.assertEqual(len(UntangledTextures.textures(BLOCK, 0)), donor.TEXTURE_COUNT)
        tex = mb.ModelBlock(BLOCK).pointer('TEX')
        for cut in (0x0E, tex + 1, tex + 4 + 0x17, tex + 4 + 0x19):
            with self.subTest(cut=cut), self.assertRaises(struct.error):
                UntangledTextures.textures(BLOCK[:cut], 0)
        unknown = bytearray(BLOCK)
        unknown[tex + 4 + 0x17] = 7
        with self.assertRaises(KeyError) as raised:
            UntangledTextures.textures(bytes(unknown), 0)
        self.assertEqual(raised.exception.args, (7,))

    def test_validator_reports_a_cut_gpl_header_with_the_struct_error_text(self):
        block = bytearray(0x40)
        struct.pack_into('>I', block, 0x04, 0x30)   # GPL header would run past the block
        report = BlockValidator.validate_model_block(bytes(block))
        self.assertFalse(report['valid'])
        failed = [error for error in report['errors'] if error.startswith('GPL header parse failed')]
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].startswith('GPL header parse failed: error: unpack_from requires a buffer'),
                        failed[0])

    def test_validator_accepts_the_donor(self):
        report = BlockValidator.validate_model_block(BLOCK)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['facts']['section_pointers'], mb.ModelBlock(BLOCK).pointers())
        self.assertEqual(report['facts']['tex_texture_count'], donor.TEXTURE_COUNT)
        self.assertEqual(report['facts']['skn_bone_ids'], [SKN_ACC_BLOCK_BONE, donor.SKINNED_BONE])


if __name__ == '__main__':
    unittest.main()
