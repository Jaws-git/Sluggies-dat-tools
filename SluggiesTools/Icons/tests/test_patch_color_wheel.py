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


if __name__ == '__main__':
    unittest.main()