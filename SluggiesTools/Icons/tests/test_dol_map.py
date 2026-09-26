import struct
import unittest

from SluggiesTools.Icons import dol_map


TEXT_ADDRESS = 0x80004000
DATA_ADDRESS = 0x80010000
BSS_ADDRESS = 0x80020000
BSS_SIZE = 0x1000


def lis(register, value):
    return (15 << 26) | (register << 21) | (value & 0xFFFF)


def addi(target, source, value):
    return (14 << 26) | (target << 21) | (source << 16) | (value & 0xFFFF)


def ori(target, source, value):
    return (24 << 26) | (source << 21) | (target << 16) | (value & 0xFFFF)


def lwz(target, base, offset):
    return (32 << 26) | (target << 21) | (base << 16) | (offset & 0xFFFF)


def stw(source, base, offset):
    return (36 << 26) | (source << 21) | (base << 16) | (offset & 0xFFFF)


def bl(source, target):
    return (18 << 26) | ((target - source) & 0x3FFFFFC) | 1


BLR = 0x4E800020
NOP = 0x60000000


def make_dol(text_words, data=b'\x11' * 0x40, entry=TEXT_ADDRESS):
    """Build a DOL with T0 = ``text_words``, D0 = ``data`` and a bss range."""
    text = b''.join(struct.pack('>I', word) for word in text_words)
    header = bytearray(dol_map.HEADER_SIZE)
    text_offset = dol_map.HEADER_SIZE
    data_offset = text_offset + len(text)
    struct.pack_into('>I', header, 0x00, text_offset)
    struct.pack_into('>I', header, 0x48, TEXT_ADDRESS)
    struct.pack_into('>I', header, 0x90, len(text))
    data_slot = dol_map.TEXT_SLOTS
    struct.pack_into('>I', header, data_slot * 4, data_offset)
    struct.pack_into('>I', header, 0x48 + data_slot * 4, DATA_ADDRESS)
    struct.pack_into('>I', header, 0x90 + data_slot * 4, len(data))
    struct.pack_into('>III', header, 0xD8, BSS_ADDRESS, BSS_SIZE, entry)
    return bytes(header) + text + data


class HeaderTests(unittest.TestCase):
    def test_slots_names_and_free_slots(self):
        header = dol_map.parse_header(make_dol([BLR]))
        self.assertEqual(len(header.slots), 18)
        self.assertEqual([slot.name for slot in header.used_slots], ['T0', 'D0'])
        self.assertEqual(
            [slot.name for slot in header.free_slots],
            ['T1', 'T2', 'T3', 'T4', 'T5', 'T6'] + [f'D{index}' for index in range(1, 11)],
        )

    def test_bss_and_highest_loaded(self):
        header = dol_map.parse_header(make_dol([BLR]))
        self.assertEqual(header.bss_end, BSS_ADDRESS + BSS_SIZE)
        self.assertEqual(header.highest_loaded, BSS_ADDRESS + BSS_SIZE)
        self.assertEqual(header.entry, TEXT_ADDRESS)

    def test_vaddr_to_file_and_region(self):
        dol = make_dol([BLR, NOP])
        header = dol_map.parse_header(dol)
        self.assertEqual(dol_map.vaddr_to_file(header, TEXT_ADDRESS + 4, 4), dol_map.HEADER_SIZE + 4)
        self.assertEqual(dol_map.vaddr_to_file(header, DATA_ADDRESS), dol_map.HEADER_SIZE + 8)
        self.assertIsNone(dol_map.vaddr_to_file(header, TEXT_ADDRESS + 4, 8))
        self.assertEqual(dol_map.region_of(header, DATA_ADDRESS + 1), 'D0')
        self.assertEqual(dol_map.region_of(header, BSS_ADDRESS + 0x10), 'bss')
        self.assertEqual(dol_map.region_of(header, BSS_ADDRESS + BSS_SIZE), 'unmapped')

    def test_truncated_header_and_section(self):
        with self.assertRaises(dol_map.DolMapError):
            dol_map.parse_header(bytes(0x80))
        with self.assertRaises(dol_map.DolMapError):
            dol_map.parse_header(make_dol([BLR])[:-1])


class AddressConstantTests(unittest.TestCase):
    def constants(self, words, low=0x80000000, high=0x81800000):
        dol = make_dol(words)
        return dol_map.find_address_constants(dol, dol_map.parse_header(dol), low, high)

    def test_addi_uses_signed_low_half(self):
        found = self.constants([lis(3, 0x807B), addi(3, 3, 0x8E80)])
        self.assertEqual(found, [dol_map.AddressConstant(TEXT_ADDRESS, TEXT_ADDRESS + 4, 3, 0x807A8E80)])

    def test_ori_reads_its_source_from_the_rs_field(self):
        found = self.constants([lis(1, 0x807B), ori(1, 1, 0x4E80)])
        self.assertEqual([(c.register, c.value) for c in found], [(1, 0x807B4E80)])

    def test_loads_and_stores_count_as_low_halves(self):
        found = self.constants([lis(4, 0x8063), lwz(5, 4, 0x1550), stw(6, 4, 0x1554)])
        self.assertEqual([(c.register, c.value) for c in found], [(5, 0x80631550), (6, 0x80631554)])

    def test_redefined_register_is_no_longer_the_lis_base(self):
        found = self.constants([lis(3, 0x807B), addi(3, 3, 0x4E80), addi(0, 3, 0x1F)])
        self.assertEqual([c.value for c in found], [0x807B4E80])

    def test_call_ends_the_pair(self):
        found = self.constants([lis(3, 0x807B), bl(TEXT_ADDRESS + 4, TEXT_ADDRESS), addi(3, 3, 0x10)])
        self.assertEqual(found, [])

    def test_pair_window_and_value_range(self):
        far = [lis(3, 0x807B)] + [NOP] * dol_map.PAIR_WINDOW + [addi(4, 3, 0x10)]
        self.assertEqual(self.constants(far), [])
        self.assertEqual(self.constants([lis(3, 0x807B), addi(4, 3, 0x10)], high=0x807B0000), [])

    def test_data_slots_are_not_scanned(self):
        data = struct.pack('>II', lis(3, 0x807B), addi(4, 3, 0x10))
        dol = make_dol([BLR], data=data)
        self.assertEqual(dol_map.find_address_constants(dol, dol_map.parse_header(dol), 0, 0x100000000), [])


class BootLayoutTests(unittest.TestCase):
    def test_initial_stack_pointer_ignores_other_registers(self):
        words = [lis(3, 0x8090), ori(3, 3, 0x0001), lis(1, 0x8002), ori(1, 1, 0x2000), BLR]
        dol = make_dol(words)
        stack = dol_map.initial_stack_pointer(dol, dol_map.parse_header(dol))
        self.assertEqual((stack.lis_site, stack.value), (TEXT_ADDRESS + 8, 0x80022000))

    def test_missing_stack_pointer(self):
        dol = make_dol([BLR])
        self.assertIsNone(dol_map.initial_stack_pointer(dol, dol_map.parse_header(dol)))

    def test_check_arena_lo_sites(self):
        words = [lis(3, 0x8002), addi(3, 3, 0x3000), NOP, NOP, BLR]
        dol = make_dol(words)
        sites = {TEXT_ADDRESS: 0x80023000, TEXT_ADDRESS + 8: 0x80024000}
        self.assertEqual(
            dol_map.check_arena_lo_sites(dol, dol_map.parse_header(dol), sites),
            {TEXT_ADDRESS: 0x80023000, TEXT_ADDRESS + 8: None},
        )


if __name__ == '__main__':
    unittest.main()
