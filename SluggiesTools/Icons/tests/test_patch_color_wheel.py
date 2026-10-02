import struct
import unittest

from SluggiesTools.Icons import patch_color_wheel as color_wheel


TABLE_OFFSET = 0x20
ENTRIES = [
    color_wheel.ColorWheelEntry('A', 0x48, bytes.fromhex('0b06060000070109')),
    color_wheel.ColorWheelEntry('B', 0x49, bytes.fromhex('020d0d0000050105')),
    color_wheel.ColorWheelEntry('C', 0x4A, bytes.fromhex('0515150000030105')),
    color_wheel.ColorWheelEntry('D', 0x4B, bytes.fromhex('0a3a240000040105')),
    color_wheel.ColorWheelEntry('E', 0x47, bytes.fromhex('0b06060000060105')),
    color_wheel.ColorWheelEntry('F', 0x4C, bytes.fromhex('010c0c0000020105')),
]


def make_stock_dol():
    dol = bytearray(TABLE_OFFSET + color_wheel.COLOR_WHEEL_COUNT * color_wheel.COLOR_WHEEL_STRIDE)
    for entry in ENTRIES:
        offset = color_wheel._row_offset(entry.char_id, TABLE_OFFSET)
        stock_row = bytearray(entry.row)
        stock_row[6] = 0
        dol[offset:offset + color_wheel.COLOR_WHEEL_STRIDE] = stock_row
    return bytes(dol)


class PatchColorWheelTests(unittest.TestCase):
    def test_patches_only_configured_rows(self):
        stock = make_stock_dol()
        updated, changed_count = color_wheel.patch_color_wheel(
            stock, stock, ENTRIES, TABLE_OFFSET
        )

        self.assertEqual(changed_count, 6)
        color_wheel.validate_color_wheel(updated, ENTRIES, TABLE_OFFSET)
        target_offsets = {
            color_wheel._row_offset(entry.char_id, TABLE_OFFSET) + index
            for entry in ENTRIES
            for index in range(color_wheel.COLOR_WHEEL_STRIDE)
        }
        self.assertTrue(all(
            before == after or index in target_offsets
            for index, (before, after) in enumerate(zip(stock, updated))
        ))

    def test_is_idempotent(self):
        stock = make_stock_dol()
        updated, _ = color_wheel.patch_color_wheel(stock, stock, ENTRIES, TABLE_OFFSET)

        repeated, changed_count = color_wheel.patch_color_wheel(
            updated, stock, ENTRIES, TABLE_OFFSET
        )

        self.assertEqual(repeated, updated)
        self.assertEqual(changed_count, 0)

    def test_rejects_unexpected_existing_row(self):
        stock = make_stock_dol()
        modified = bytearray(stock)
        offset = color_wheel._row_offset(0x48, TABLE_OFFSET)
        modified[offset] ^= 0x01

        with self.assertRaisesRegex(color_wheel.ColorWheelPatchError, 'unexpected row'):
            color_wheel.patch_color_wheel(bytes(modified), stock, ENTRIES, TABLE_OFFSET)

    def test_rejects_truncated_dol(self):
        with self.assertRaisesRegex(color_wheel.ColorWheelPatchError, 'truncated'):
            color_wheel.patch_color_wheel(b'', b'', ENTRIES, TABLE_OFFSET)


class SelectableAndCapTests(unittest.TestCase):
    def test_earlier_run_with_other_selectable_value_is_accepted(self):
        stock = make_stock_dol()
        updated, _ = color_wheel.patch_color_wheel(stock, stock, ENTRIES, TABLE_OFFSET)
        off_wheel = [color_wheel.ColorWheelEntry(e.name, e.char_id, e.row[:6] + bytes(1) + e.row[7:])
                     if e.char_id == 0x48 else e for e in ENTRIES]
        again, changed = color_wheel.patch_color_wheel(updated, stock, off_wheel, TABLE_OFFSET)
        self.assertEqual(changed, 1)
        self.assertEqual(again[color_wheel._row_offset(0x48, TABLE_OFFSET) + 6], 0)

    def cap_dol(self, yoshis: int) -> bytes:
        # header: T0 over the two cap sites' region is too far apart, so use two text slots
        dol = bytearray(color_wheel.COLOR_WHEEL_OFFSET + color_wheel.COLOR_WHEEL_COUNT * 8)
        sites = sorted(color_wheel.WHEEL_CAP_SITES)
        for slot, address in enumerate(sites):
            file_offset = 0x100 + slot * 0x10
            struct.pack_into('>I', dol, slot * 4, file_offset)
            struct.pack_into('>I', dol, 0x48 + slot * 4, address)
            struct.pack_into('>I', dol, 0x90 + slot * 4, 4)
            struct.pack_into('>I', dol, file_offset, color_wheel.WHEEL_CAP_SITES[address])
        for char_id in range(yoshis):
            offset = color_wheel.COLOR_WHEEL_OFFSET + char_id * 8
            dol[offset:offset + 8] = bytes([0x0B, 6, 6, 0, 0, char_id, 1, 0])
        return bytes(dol)

    def caps(self, dol):
        return [struct.unpack_from('>I', dol, 0x100 + slot * 0x10)[0] & 0xFFFF for slot in range(2)]

    def test_caps_follow_the_largest_wheel(self):
        six, changed = color_wheel.patch_wheel_caps(self.cap_dol(6))
        self.assertEqual((self.caps(six), changed), ([6, 6], 0))
        seven, changed = color_wheel.patch_wheel_caps(self.cap_dol(7))
        self.assertEqual((self.caps(seven), changed), ([7, 7], 2))
        again, changed = color_wheel.patch_wheel_caps(seven)
        self.assertEqual(changed, 0)

    def test_eight_members_refused(self):
        with self.assertRaisesRegex(color_wheel.ColorWheelPatchError, 'more than 7'):
            color_wheel.patch_wheel_caps(self.cap_dol(8))


if __name__ == '__main__':
    unittest.main()