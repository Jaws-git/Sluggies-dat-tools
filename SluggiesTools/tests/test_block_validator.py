import pathlib
import struct
import sys
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import BlockValidator
from BlockValidator import GPL_MAGIC, validate_model_block


def _u16_to_bytes(values):
    return b''.join(struct.pack('>H', value) for value in values)


def _write_ptr7_facial(
    block: bytearray,
    *,
    ptr7_offset: int = 0x3C0,
    submesh_index: int = 0,
    first_vertex: int = 0,
    vertex_count: int = 1,
    maximum_pose_count: int = 1,
    object_pose_count: int = 1,
) -> None:
    struct.pack_into('>I', block, 0x18, ptr7_offset)

    section = bytearray(0x40)
    struct.pack_into('>H', section, 0x00, maximum_pose_count)
    struct.pack_into('>H', section, 0x02, 1)      # object count
    struct.pack_into('>H', section, 0x04, 2)      # attribute type count
    struct.pack_into('>I', section, 0x08, 0x0C)   # object table offset

    # Single object table entry.
    struct.pack_into('>H', section, 0x0C, object_pose_count)
    struct.pack_into('>H', section, 0x0E, 1)      # attribute count
    struct.pack_into('>I', section, 0x10, 0x0C + object_pose_count * 4)  # attribute record size
    struct.pack_into('>I', section, 0x14, 0x18)   # object data offset

    # Single attribute record (position kind).
    struct.pack_into('>I', section, 0x18, 1)      # entry count
    section[0x1C] = submesh_index
    section[0x1D] = 1                              # position attribute kind
    section[0x1E] = 3                              # component count
    section[0x1F] = 2                              # component size
    run_list_offset = 0x2C
    struct.pack_into('>I', section, 0x20, run_list_offset)   # run list offset

    pose_base = 0x30
    for pose_index in range(object_pose_count):
        struct.pack_into('>I', section, 0x24 + pose_index * 4, pose_base)

    # One run and one 3xint16 pose sample.
    struct.pack_into('>H', section, run_list_offset, first_vertex)
    struct.pack_into('>H', section, run_list_offset + 2, vertex_count)
    section[pose_base:pose_base + 6] = b'\x00\x00\x00\x00\x00\x00'

    block[ptr7_offset:ptr7_offset + len(section)] = section


def make_valid_block() -> bytes:
    block = bytearray(0x400)

    # Model header pointers.
    struct.pack_into('>8I', block, 0x00,
        0,
        0x20,   # GPL
        0x2A0,  # ACT
        0x2B0,  # TEX
        0x2C0,  # SKN
        0, 0, 0,
    )

    gpl = 0x20
    struct.pack_into('>I', block, gpl + 0x00, GPL_MAGIC)
    struct.pack_into('>I', block, gpl + 0x0C, 2)       # 2 submeshes
    struct.pack_into('>I', block, gpl + 0x10, 0x14)    # descriptor table rel

    # Descriptor table (2 x 8 bytes): layout ptr + name ptr.
    struct.pack_into('>I', block, gpl + 0x14, 0x24)    # sub0 layout rel
    struct.pack_into('>I', block, gpl + 0x18, 0x00)
    struct.pack_into('>I', block, gpl + 0x1C, 0x140)   # sub1 layout rel
    struct.pack_into('>I', block, gpl + 0x20, 0x00)

    # Submesh 0 layout (skinned positions cc=6).
    l0 = gpl + 0x24
    struct.pack_into('>I', block, l0 + 0x00, 0x20)   # pos header rel
    struct.pack_into('>I', block, l0 + 0x04, 0x28)   # col header rel
    struct.pack_into('>I', block, l0 + 0x08, 0x30)   # uv header rel
    struct.pack_into('>I', block, l0 + 0x0C, 0x40)   # nor header rel
    struct.pack_into('>I', block, l0 + 0x10, 0x50)   # ds header rel
    block[l0 + 0x14] = 0

    # pos header: raw=0x5C => abs 0xA0 (32-aligned), cnt=1, q=0x30(float), cc=6
    struct.pack_into('>I', block, l0 + 0x20, 0x5C)
    struct.pack_into('>H', block, l0 + 0x24, 1)
    block[l0 + 0x26] = 0x30
    block[l0 + 0x27] = 6

    # color header (unused)
    struct.pack_into('>I', block, l0 + 0x28, 0)
    struct.pack_into('>H', block, l0 + 0x2C, 0)
    block[l0 + 0x2E] = 0
    block[l0 + 0x2F] = 4

    # uv header (unused)
    struct.pack_into('>I', block, l0 + 0x30, 0)
    struct.pack_into('>H', block, l0 + 0x34, 0)
    block[l0 + 0x36] = 0x10
    block[l0 + 0x37] = 2

    # normal header (unused standalone)
    struct.pack_into('>I', block, l0 + 0x40, 0)
    struct.pack_into('>H', block, l0 + 0x44, 0)
    block[l0 + 0x46] = 0x30
    block[l0 + 0x47] = 3

    # display-state header: table at rel 0x60, count=1
    struct.pack_into('>I', block, l0 + 0x50, 0x00)
    struct.pack_into('>I', block, l0 + 0x54, 0x60)
    struct.pack_into('>H', block, l0 + 0x58, 1)

    ds0 = l0 + 0x60
    block[ds0 + 0x00] = 3
    struct.pack_into('>I', block, ds0 + 0x04, 0x00000008)  # Type-3: position indexed u8
    struct.pack_into('>I', block, ds0 + 0x08, 0x7C)  # prim rel => abs 0xC0 (aligned)
    struct.pack_into('>I', block, ds0 + 0x0C, 32)
    block[l0 + 0x7C:l0 + 0x9C] = b'\x90\x00\x01\x00' + b'\x00' * 28

    # Skinned position payload.
    block[l0 + 0x5C:l0 + 0x74] = b'\x00' * 0x18

    # Submesh 1 layout (non-skinned position only, outside scratch window).
    l1 = gpl + 0x140
    struct.pack_into('>I', block, l1 + 0x00, 0x20)
    struct.pack_into('>I', block, l1 + 0x04, 0x28)
    struct.pack_into('>I', block, l1 + 0x08, 0x30)
    struct.pack_into('>I', block, l1 + 0x0C, 0x40)
    struct.pack_into('>I', block, l1 + 0x10, 0x50)
    block[l1 + 0x14] = 0

    struct.pack_into('>I', block, l1 + 0x20, 0xA0)  # abs 0x200
    struct.pack_into('>H', block, l1 + 0x24, 1)
    block[l1 + 0x26] = 0x30
    block[l1 + 0x27] = 3
    block[l1 + 0xA0:l1 + 0xAC] = b'\x00' * 12

    struct.pack_into('>I', block, l1 + 0x28, 0)
    struct.pack_into('>I', block, l1 + 0x30, 0)
    struct.pack_into('>I', block, l1 + 0x40, 0)
    struct.pack_into('>I', block, l1 + 0x50, 0x00)
    struct.pack_into('>I', block, l1 + 0x54, 0x60)
    struct.pack_into('>H', block, l1 + 0x58, 0)

    # ACT/TEX placeholders.
    block[0x2A0:0x2B0] = b'A' * 0x10
    struct.pack_into('>H', block, 0x2B0, 0)  # num TPL
    struct.pack_into('>H', block, 0x2B2, 0)  # num CLUT

    # SKN section.
    skn = 0x2C0
    struct.pack_into('>H', block, skn + 0x00, 0)    # SK1 count
    struct.pack_into('>H', block, skn + 0x02, 0)    # SK2 count
    struct.pack_into('>H', block, skn + 0x04, 1)    # SKAcc count
    block[skn + 0x06] = 0x30                         # float stride=24
    struct.pack_into('>I', block, skn + 0x08, 0)
    struct.pack_into('>I', block, skn + 0x0C, 0)
    struct.pack_into('>I', block, skn + 0x10, 0x24) # SKAcc structs start
    struct.pack_into('>I', block, skn + 0x14, 0)    # memClrPtr
    struct.pack_into('>I', block, skn + 0x18, 0x20) # memClrSize (write end 0x20)
    struct.pack_into('>I', block, skn + 0x1C, 0xA0) # flush ptr
    struct.pack_into('>I', block, skn + 0x20, 1)    # flush size (u16 count)

    acc0 = skn + 0x24
    struct.pack_into('>I', block, acc0 + 0x30, 0x80)  # src abs 0x340 aligned
    struct.pack_into('>I', block, acc0 + 0x34, 0xC0)  # dst abs 0x380 aligned
    struct.pack_into('>I', block, acc0 + 0x38, 0)     # gda
    struct.pack_into('>I', block, acc0 + 0x3C, 0xE0)  # wt abs 0x3A0 aligned
    struct.pack_into('>H', block, acc0 + 0x42, 1)     # vertex count

    block[skn + 0x80:skn + 0x98] = b'\x00' * 0x18     # SKAcc src
    block[skn + 0xC0:skn + 0xC2] = _u16_to_bytes([0])  # SKAcc dst idx
    block[skn + 0xE0] = 0xFF                           # SKAcc wt
    block[skn + 0xA0:skn + 0xA2] = _u16_to_bytes([0])  # flush entry

    return bytes(block)


def make_valid_textured_block(texture_count: int = 1) -> bytearray:
    original = bytearray(make_valid_block())
    block = bytearray(0x500)
    block[:0x2B0] = original[:0x2B0]
    skn = 0x340
    struct.pack_into('>I', block, 0x10, skn)
    block[skn:skn + 0x140] = original[0x2C0:0x400]

    tex = 0x2B0
    struct.pack_into('>HH', block, tex, texture_count, 0)
    data_ptr = 0x60
    for index in range(texture_count):
        desc = tex + 4 + index * 0x20
        struct.pack_into('>I', block, desc, data_ptr + index * 8)
        struct.pack_into('>HH', block, desc + 8, 4, 4)
        block[desc + 0x17] = 0xE
        block[tex + data_ptr + index * 8:tex + data_ptr + index * 8 + 8] = b'\xAA' * 8
    return block


class BlockValidatorTests(unittest.TestCase):
    def test_valid_block_passes(self):
        report = validate_model_block(make_valid_block())
        self.assertTrue(report['valid'])
        self.assertEqual(report['errors'], [])
        self.assertIn('section_pointers', report['facts'])

    def test_header_pointer_outside_block_fails(self):
        block = bytearray(make_valid_block())
        struct.pack_into('>I', block, 0x0C, 0xFFFF)  # TEX pointer out of bounds
        report = validate_model_block(bytes(block))
        self.assertFalse(report['valid'])
        self.assertTrue(any('pointer outside block' in error for error in report['errors']))

    def test_section_order_violation_fails(self):
        block = bytearray(make_valid_block())
        struct.pack_into('>I', block, 0x08, 0x100)   # ACT before GPL descriptor area
        struct.pack_into('>I', block, 0x0C, 0x0F0)   # TEX lower than ACT
        report = validate_model_block(bytes(block))
        self.assertFalse(report['valid'])
        self.assertTrue(any('out of order' in error for error in report['errors']))

    def test_misaligned_skn_array_fails(self):
        block = bytearray(make_valid_block())
        skn = 0x2C0
        acc0 = skn + 0x24
        struct.pack_into('>I', block, acc0 + 0x30, 0x82)  # SKAcc src misaligned
        report = validate_model_block(bytes(block))
        self.assertFalse(report['valid'])
        self.assertTrue(any('SKAcc[0] source array not 32-byte aligned' in error for error in report['errors']))

    def test_unpadded_primitive_list_size_fails(self):
        block = bytearray(make_valid_block())
        ds0 = 0x20 + 0x24 + 0x60
        block[ds0 + 0x00] = 3
        struct.pack_into('>I', block, ds0 + 0x04, 0x00000008)
        struct.pack_into('>I', block, ds0 + 0x08, 0x7C)
        struct.pack_into('>I', block, ds0 + 0x0C, 4)
        block[0xC0:0xC4] = b'\x90\x00\x01\x00'

        report = validate_model_block(bytes(block))

        self.assertFalse(report['valid'])
        self.assertTrue(any(
            'primitive list size 4 is not a multiple of 32 bytes' in error
            for error in report['errors']
        ))

    def test_incorrect_position_relative_memclr_range_fails(self):
        block = bytearray(make_valid_block())
        struct.pack_into('>I', block, 0x2C0 + 0x14, 0x20)

        report = validate_model_block(bytes(block))

        self.assertFalse(report['valid'])
        self.assertTrue(any('expected position-data-relative' in error for error in report['errors']))

    def test_scratch_overlap_fails(self):
        block = bytearray(make_valid_block())
        # Place sub0 color array inside SK write window (sub0 starts at 0xA0, window end 0xC0).
        l0 = 0x20 + 0x24
        struct.pack_into('>I', block, l0 + 0x28, 0x70)  # color abs 0xB4
        struct.pack_into('>H', block, l0 + 0x2C, 1)
        block[l0 + 0x2E] = 0x00
        block[l0 + 0x2F] = 4
        block[l0 + 0x70:l0 + 0x72] = b'\x12\x34'
        report = validate_model_block(bytes(block))
        self.assertFalse(report['valid'])
        self.assertTrue(any('scratch window overlaps sub0.col' in error for error in report['errors']))

    def test_invalid_position_quantize_format_fails(self):
        block = bytearray(make_valid_block())
        l0 = 0x20 + 0x24
        block[l0 + 0x26] = 0x60  # unsupported format nibble 6
        report = validate_model_block(bytes(block))
        self.assertFalse(report['valid'])
        self.assertTrue(any('unsupported quantize format nibble 6' in error for error in report['errors']))

    def test_descriptor_width_too_narrow_fails(self):
        block = bytearray(make_valid_block())
        l0 = 0x20 + 0x24

        # Enable one UV channel with >255 entries so u8 texture indices are insufficient.
        block[l0 + 0x14] = 1
        struct.pack_into('>I', block, l0 + 0x30, 0x00)
        struct.pack_into('>H', block, l0 + 0x34, 257)
        block[l0 + 0x36] = 0x30
        block[l0 + 0x37] = 2

        # Move DS table to a non-overlapping area so the synthetic primitive stream is visible.
        struct.pack_into('>I', block, l0 + 0x54, 0xA0)
        struct.pack_into('>H', block, l0 + 0x58, 1)
        ds0 = l0 + 0xA0

        # Type-3 descriptor uses u8 for position and texture0.
        block[ds0 + 0x00] = 3
        struct.pack_into('>I', block, ds0 + 0x04, 0x00000808)
        struct.pack_into('>I', block, ds0 + 0x08, 0xC0)
        struct.pack_into('>I', block, ds0 + 0x0C, 6)
        block[l0 + 0xC0:l0 + 0xC6] = b'\x90\x00\x01\x00\x00\x00'

        report = validate_model_block(bytes(block))
        self.assertFalse(report['valid'])
        self.assertTrue(any('requires wider index width' in error for error in report['errors']))

    def test_ptr7_facial_submesh_reference_out_of_range_fails(self):
        block = bytearray(make_valid_block())
        _write_ptr7_facial(block, submesh_index=9)

        report = validate_model_block(bytes(block))
        self.assertFalse(report['valid'])
        self.assertTrue(any('references submesh 9' in error for error in report['errors']))

    def test_ptr7_facial_object_pose_count_can_exceed_section_max(self):
        block = bytearray(make_valid_block())
        _write_ptr7_facial(block, maximum_pose_count=1, object_pose_count=2, submesh_index=1)

        report = validate_model_block(bytes(block))
        self.assertTrue(report['valid'])
        self.assertEqual(report['errors'], [])

    def test_valid_unaligned_tex_image_payload_passes(self):
        block = make_valid_textured_block(texture_count=2)
        report = validate_model_block(bytes(block))
        self.assertTrue(report['valid'], report['errors'])

    def test_tex_zero_image_pointer_fails(self):
        block = make_valid_textured_block()
        struct.pack_into('>I', block, 0x2B0 + 4, 0)
        report = validate_model_block(bytes(block))
        self.assertTrue(any('zero image pointer' in error for error in report['errors']))

    def test_tex_overlapping_payloads_fail(self):
        block = make_valid_textured_block(texture_count=2)
        first_ptr = struct.unpack_from('>I', block, 0x2B0 + 4)[0]
        struct.pack_into('>I', block, 0x2B0 + 4 + 0x20, first_ptr + 4)
        report = validate_model_block(bytes(block))
        self.assertTrue(any('TEX payload overlap' in error for error in report['errors']))

    def test_tex_same_start_image_alias_passes(self):
        block = make_valid_textured_block(texture_count=2)
        first_ptr = struct.unpack_from('>I', block, 0x2B0 + 4)[0]
        struct.pack_into('>I', block, 0x2B0 + 4 + 0x20, first_ptr)
        report = validate_model_block(bytes(block))
        self.assertTrue(report['valid'], report['errors'])

    def test_tex_payload_crossing_section_boundary_fails(self):
        block = make_valid_textured_block()
        struct.pack_into('>I', block, 0x2B0 + 4, 0x89)
        report = validate_model_block(bytes(block))
        self.assertTrue(any('image payload exceeds TEX section' in error for error in report['errors']))

    def test_tex_palette_consistency_and_clut_count_fail(self):
        block = make_valid_textured_block()
        desc = 0x2B0 + 4
        struct.pack_into('>I', block, desc + 4, 0x70)
        struct.pack_into('>H', block, desc + 0x18, 1)
        report = validate_model_block(bytes(block))
        self.assertTrue(any('direct format must not have a palette' in error for error in report['errors']))
        self.assertTrue(any('CLUT count 0 does not match 1' in error for error in report['errors']))

    def test_type1_texture_index_outside_tex_count_fails(self):
        block = make_valid_textured_block(texture_count=1)
        ds0 = 0x20 + 0x24 + 0x60
        block[ds0 + 0x00] = 1
        struct.pack_into('>I', block, ds0 + 0x04, 0x11110001)
        struct.pack_into('>II', block, ds0 + 0x08, 0, 0)

        report = validate_model_block(bytes(block))

        self.assertFalse(report['valid'])
        self.assertTrue(any(
            'Type-1 texture index 1 is outside TEX count 1' in error
            for error in report['errors']
        ))


def _make_skn_two_vertex_block(
    *,
    sk2_weights=(128, 128),
    sk2_gpl_vertex_arr_value=None,
    sk2_vertex_offset=None,
    n_verts=4,
    mem_clr=(0, 0),
):
    """A minimal, self-contained SKN section with 2 skinned entries: SK1 bone
    10 owns slot 0, SK2 pair (20, 21) owns slot 3 (gplVertexArr 0x20 +
    vertexOffset 4, donor-style: each entry on its own cache line, slots 1-2
    unused). quantize_info 0x30 (nibble 3) => comp_size 2 => stride 12."""
    stride = 12
    block = bytearray(0x140)

    struct.pack_into('>H', block, 0x00, 1)     # SK1 count
    struct.pack_into('>H', block, 0x02, 1)     # SK2 count
    struct.pack_into('>H', block, 0x04, 0)     # SKAcc count
    block[0x06] = 0x30                          # quantize info
    struct.pack_into('>I', block, 0x08, 0x24)  # SK1 struct array offset
    struct.pack_into('>I', block, 0x0C, 0x64)  # SK2 struct array offset
    struct.pack_into('>I', block, 0x10, 0)     # SKAcc struct array offset (unused)
    struct.pack_into('>I', block, 0x14, mem_clr[0])  # memClrPtr
    struct.pack_into('>I', block, 0x18, mem_clr[1])  # memClrSize
    struct.pack_into('>I', block, 0x1C, 0)     # flush ptr
    struct.pack_into('>I', block, 0x20, 0)     # flush size

    sk1 = 0x24
    struct.pack_into('>I', block, sk1 + 0x30, 0xE0)   # source ptr
    struct.pack_into('>I', block, sk1 + 0x34, 0)      # GplVertexArrValue (slot 0)
    struct.pack_into('>H', block, sk1 + 0x38, 10)     # BoneIndex
    struct.pack_into('>H', block, sk1 + 0x3A, 1)      # VertexCnt
    block[sk1 + 0x3C] = 0                              # VertexOffset

    sk2 = 0x64
    struct.pack_into('>I', block, sk2 + 0x60, 0x100)  # source ptr
    struct.pack_into('>I', block, sk2 + 0x64, 0x120)  # weight ptr
    gva2 = 0x20 if sk2_gpl_vertex_arr_value is None else sk2_gpl_vertex_arr_value
    vo2 = (4 if sk2_gpl_vertex_arr_value is None else 0) if sk2_vertex_offset is None         else sk2_vertex_offset
    struct.pack_into('>I', block, sk2 + 0x68, gva2)   # GplVertexArrValue
    struct.pack_into('>H', block, sk2 + 0x6C, 20)     # BoneIndex1
    struct.pack_into('>H', block, sk2 + 0x6E, 21)     # BoneIndex2
    struct.pack_into('>H', block, sk2 + 0x70, 1)      # VertexCnt
    block[sk2 + 0x72] = vo2                            # VertexOffset

    block[0xE0:0xE0 + stride] = b'\x01' * stride       # SK1 bind-pose source
    block[0x100:0x100 + stride] = b'\x02' * stride     # SK2 bind-pose source
    block[0x120] = sk2_weights[0]
    block[0x121] = sk2_weights[1]

    facts_layout = [{'position_comp_count': 6, 'position_count': n_verts}]
    return bytes(block), facts_layout


class SKNMembershipCoverageTests(unittest.TestCase):
    """PLAN_ModelReplacements.md 3.4: BlockValidator must independently catch
    a same-count reskin that left the built SKN section with a bad weight
    encoding, a duplicated destination slot, or a vertex with no direct
    (SK1/SK2) bone influence."""

    def _validate(self, block, facts_layout):
        state = BlockValidator._ValidationState(block)
        state.facts['section_ranges']['SKN'] = {'start': 0, 'end': len(block)}
        state.facts['gpl_submesh_layout'] = facts_layout
        BlockValidator._validate_skn(state, [], [])
        return state.errors

    def test_valid_two_vertex_fixture_passes(self):
        block, layout = _make_skn_two_vertex_block()
        self.assertEqual(self._validate(block, layout), [])

    def test_sk2_weight_pair_not_summing_to_256_is_not_an_error(self):
        # A vertex with an SKAcc supplement splits its 256-unit budget across
        # the SK2 pair AND the SKAcc weight(s) together — verified against
        # production data (Luigi), where donor SK2 pairs legitimately sum to
        # well under 256. No sum check is enforced.
        block, layout = _make_skn_two_vertex_block(sk2_weights=(100, 100))
        self.assertEqual(self._validate(block, layout), [])

    def test_duplicate_direct_write_slot_fails(self):
        block, layout = _make_skn_two_vertex_block(sk2_gpl_vertex_arr_value=0)
        errors = self._validate(block, layout)
        self.assertTrue(any(
            'claim 1 position slot(s) more than once' in error for error in errors
        ))

    def test_uncovered_vertex_slot_is_not_an_error(self):
        # Real donor models legitimately leave some submesh-0 vertices with
        # no SK1/SK2/SKAcc coverage at all (verified against production data:
        # Luigi has 130 of 2808 submesh-0 vertices with zero coverage) — a
        # vertex slot beyond every SK1/SK2 entry's range must not fail.
        block, layout = _make_skn_two_vertex_block(n_verts=5)
        self.assertEqual(self._validate(block, layout), [])


class SKNDestinationLayoutTests(unittest.TestCase):
    """Donor destination layout rules, verified with zero exceptions across
    361 skinned exports (12,364 SK1/SK2 entries): gplVertexArr on a cache-line
    boundary, first write on a vertex boundary, no cache line shared by two
    SK1/SK2 entries, and memClr never touching a direct entry's lines."""

    def _validate(self, block, facts_layout):
        state = BlockValidator._ValidationState(block)
        state.facts['section_ranges']['SKN'] = {'start': 0, 'end': len(block)}
        state.facts['gpl_submesh_layout'] = facts_layout
        BlockValidator._validate_skn(state, [], [])
        return state

    def test_gpl_vertex_arr_off_cache_line_fails(self):
        # slot 3 addressed as 0x24 + 0 instead of 0x20 + 4
        block, layout = _make_skn_two_vertex_block(sk2_gpl_vertex_arr_value=0x24)
        errors = self._validate(block, layout).errors
        self.assertTrue(any('not on a cache-line boundary' in error for error in errors))

    def test_first_write_off_vertex_boundary_fails(self):
        block, layout = _make_skn_two_vertex_block(sk2_vertex_offset=0)
        errors = self._validate(block, layout).errors
        self.assertTrue(any('not on a 12-byte vertex boundary' in error for error in errors))

    def test_entries_sharing_a_cache_line_fail(self):
        # slot 1 (0x0C) shares cache line 0x0 with SK1's slot 0
        block, layout = _make_skn_two_vertex_block(
            sk2_gpl_vertex_arr_value=0, sk2_vertex_offset=12)
        errors = self._validate(block, layout).errors
        self.assertTrue(any('share 1 cache line(s): 0x0 (SK1[0]/SK2[0])' in error
                            for error in errors))

    def test_vertex_offset_skipping_a_whole_vertex_warns(self):
        state = self._validate(*_make_skn_two_vertex_block(
            sk2_gpl_vertex_arr_value=0x20, sk2_vertex_offset=16))
        self.assertTrue(any('skips a whole vertex' in warning for warning in state.warnings))

    def test_source_array_off_mirror_fails(self):
        # SK2 source moved 0x20 past base + gplVertexArr (sequential packing
        # dropping a donor gap line) — the Luigi facial-pose neck stretch.
        block, layout = _make_skn_two_vertex_block()
        block = bytearray(block)
        struct.pack_into('>I', block, 0x64 + 0x60, 0x120)
        block[0x120:0x120 + 12] = b'' * 12
        errors = self._validate(bytes(block), layout).errors
        self.assertTrue(any(
            'do not mirror the position buffer for 1 SK1/SK2' in error
            and 'SK2[0] (source 0x120, expected 0x100)' in error
            for error in errors
        ))

    def test_mem_clear_touching_direct_entry_line_fails(self):
        block, layout = _make_skn_two_vertex_block(mem_clr=(0x30, 0x20))
        errors = self._validate(block, layout).errors
        self.assertTrue(any('touches cache lines of direct writes: SK2[0]' in error
                            for error in errors))


if __name__ == '__main__':
    unittest.main()