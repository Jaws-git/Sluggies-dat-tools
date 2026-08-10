import pathlib
import struct
import sys
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

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
) -> None:
    struct.pack_into('>I', block, 0x18, ptr7_offset)

    section = bytearray(0x40)
    struct.pack_into('>H', section, 0x00, 1)      # max pose count
    struct.pack_into('>H', section, 0x02, 1)      # object count
    struct.pack_into('>H', section, 0x04, 2)      # attribute type count
    struct.pack_into('>I', section, 0x08, 0x0C)   # object table offset

    # Single object table entry.
    struct.pack_into('>H', section, 0x0C, 1)      # pose count
    struct.pack_into('>H', section, 0x0E, 1)      # attribute count
    struct.pack_into('>I', section, 0x10, 0x10)   # attribute record size
    struct.pack_into('>I', section, 0x14, 0x18)   # object data offset

    # Single attribute record (position kind).
    struct.pack_into('>I', section, 0x18, 1)      # entry count
    section[0x1C] = submesh_index
    section[0x1D] = 1                              # position attribute kind
    section[0x1E] = 3                              # component count
    section[0x1F] = 2                              # component size
    struct.pack_into('>I', section, 0x20, 0x28)   # run list offset
    struct.pack_into('>I', section, 0x24, 0x2C)   # pose offset[0]

    # One run and one 3xint16 pose sample.
    struct.pack_into('>H', section, 0x28, first_vertex)
    struct.pack_into('>H', section, 0x2A, vertex_count)
    section[0x2C:0x32] = b'\x00\x00\x00\x00\x00\x00'

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
    struct.pack_into('>I', block, ds0 + 0x0C, 4)
    block[l0 + 0x7C:l0 + 0x80] = b'\x90\x00\x01\x00'

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


if __name__ == '__main__':
    unittest.main()