import struct
import unittest

from SluggiesTools.Icons import dol_map
from SluggiesTools.Icons import dol_section_smoke as smoke
from SluggiesTools.Icons.tests.test_dol_map import (
    BLR, BSS_ADDRESS, BSS_SIZE, TEXT_ADDRESS, addi, lis, make_dol,
)


STACK_END = BSS_ADDRESS + BSS_SIZE + 0x1000
DEBUGGER_STACK_END = STACK_END + 0x2000
SECTION_ADDRESS = DEBUGGER_STACK_END
# Stock-shaped OSInit: two MEM1 pairs, then the two MEM2-path copies.
ARENA_WORDS = [
    lis(3, (DEBUGGER_STACK_END + 0x8000) >> 16), addi(3, 3, DEBUGGER_STACK_END),
    lis(3, (STACK_END + 0x8000) >> 16), addi(3, 3, STACK_END),
    lis(5, (DEBUGGER_STACK_END + 0x8000) >> 16), addi(5, 5, DEBUGGER_STACK_END),
    lis(3, (STACK_END + 0x8000) >> 16), addi(3, 3, STACK_END),
    BLR,
]
ARENA_SITES = {
    TEXT_ADDRESS: DEBUGGER_STACK_END,
    TEXT_ADDRESS + 8: STACK_END,
    TEXT_ADDRESS + 16: DEBUGGER_STACK_END,
    TEXT_ADDRESS + 24: STACK_END,
}


def stock_dol():
    return make_dol(ARENA_WORDS)


class MarkerTests(unittest.TestCase):
    def test_layout(self):
        marker = smoke.build_marker(0x807B6E80, 0x80, 0x807B6F00)
        self.assertEqual(len(marker), 0x80)
        self.assertEqual(marker[:16], smoke.MARKER_MAGIC)
        self.assertEqual(struct.unpack_from('>IIII', marker, 0x10), (0x807B6E80, 0x80, 0x807B6F00, 0))
        self.assertEqual(struct.unpack_from('>I', marker, 0x20)[0], 0x807B6EA0)
        self.assertEqual(struct.unpack_from('>I', marker, 0x6C)[0], 0x807B6EEC)
        self.assertEqual(marker[-16:], smoke.MARKER_END)

    def test_bad_sizes(self):
        for size in (0x20, 0x44):
            with self.assertRaises(smoke.DolSmokeError):
                smoke.build_marker(0x807B6E80, size, 0)


class LisAddiTests(unittest.TestCase):
    def test_round_trip_with_negative_low_half(self):
        for value in (0x807B6E80, 0x807B8E80, 0x807B4E80):
            high, low = smoke._lis_addi_words(3, value)
            self.assertEqual(smoke._lis_addi_value(high, low), (3, value))

    def test_rejects_other_shapes(self):
        self.assertIsNone(smoke._lis_addi_value(lis(3, 0x807B), addi(4, 3, 0x10)))
        self.assertIsNone(smoke._lis_addi_value(BLR, addi(3, 3, 0x10)))


class BuildSmokeDolTests(unittest.TestCase):
    def build(self, dol=None, **kwargs):
        kwargs.setdefault('address', SECTION_ADDRESS)
        kwargs.setdefault('size', 0x100)
        return smoke.build_smoke_dol(stock_dol() if dol is None else dol, arena_sites=ARENA_SITES, **kwargs)

    def test_section_is_appended_in_first_free_data_slot(self):
        source = stock_dol()
        result = self.build(source)
        header = dol_map.parse_header(result.dol)
        self.assertEqual(result.slot.name, 'D1')
        self.assertEqual(result.slot.file_offset, (len(source) + 0x1F) & ~0x1F)
        self.assertEqual(len(result.dol), result.slot.file_offset + 0x100)
        self.assertEqual(dol_map.region_of(header, SECTION_ADDRESS + 0xFF), 'D1')
        text_end = dol_map.HEADER_SIZE + len(ARENA_WORDS) * 4
        self.assertEqual(result.dol[text_end:len(source)], source[text_end:])

    def test_all_arena_constants_raised_to_section_end(self):
        result = self.build()
        self.assertEqual(result.arena_start, SECTION_ADDRESS + 0x100)
        header = dol_map.parse_header(result.dol)
        self.assertEqual(
            dol_map.check_arena_lo_sites(result.dol, header, ARENA_SITES),
            dict.fromkeys(ARENA_SITES, SECTION_ADDRESS + 0x100),
        )
        self.assertEqual(
            {site: old for site, (old, _) in result.arena_patches.items()}, ARENA_SITES,
        )
        # Registers are kept: the third pair still targets r5.
        self.assertEqual((struct.unpack_from('>I', result.dol, dol_map.HEADER_SIZE + 16)[0] >> 21) & 31, 5)

    def test_rerun_is_a_no_op(self):
        first = self.build().dol
        self.assertEqual(self.build(first).dol, first)

    def test_changed_arena_constant_is_refused(self):
        words = list(ARENA_WORDS)
        words[3] = addi(3, 3, STACK_END + 0x20)
        with self.assertRaisesRegex(smoke.DolSmokeError, 'expected stock'):
            self.build(make_dol(words))

    def test_section_below_stacks_is_refused(self):
        with self.assertRaisesRegex(smoke.DolSmokeError, 'boot stacks'):
            self.build(address=STACK_END)

    def test_section_inside_bss_is_refused(self):
        with self.assertRaisesRegex(smoke.DolSmokeError, 'highest loaded'):
            self.build(address=BSS_ADDRESS)

    def test_unaligned_address_is_refused(self):
        with self.assertRaisesRegex(smoke.DolSmokeError, 'multiples'):
            self.build(address=SECTION_ADDRESS + 4)

    def test_no_free_data_slot(self):
        dol = bytearray(stock_dol())
        for index in range(dol_map.TEXT_SLOTS + 1, dol_map.SLOT_COUNT):
            struct.pack_into('>I', dol, index * 4, dol_map.HEADER_SIZE)
            struct.pack_into('>I', dol, 0x48 + index * 4, 0x80011000 + index * 0x100)
            struct.pack_into('>I', dol, 0x90 + index * 4, 4)
        with self.assertRaisesRegex(smoke.DolSmokeError, 'no free data slot'):
            self.build(bytes(dol))

    def test_validate_detects_damaged_marker(self):
        result = self.build()
        damaged = bytearray(result.dol)
        damaged[result.slot.file_offset + 0x40] ^= 0xFF
        with self.assertRaisesRegex(smoke.DolSmokeError, 'damaged'):
            smoke.validate_smoke_dol(bytes(damaged), SECTION_ADDRESS, 0x100, ARENA_SITES)


if __name__ == '__main__':
    unittest.main()
