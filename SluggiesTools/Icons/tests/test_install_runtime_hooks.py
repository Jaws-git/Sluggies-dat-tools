import struct
import unittest

from SluggiesTools.Icons import install_runtime_hooks as hooks
from SluggiesTools.Icons.tests.test_update_icon_source_tables import ROUTES


def make_dol():
    sections = (
        (0x100, 0x80004000, 0x2460),
        (0x2560, 0x8050A000, 0x1000),
        (0x3560, 0x80519000, 0x1000),
    )
    dol = bytearray(0x4560)
    for index, (file_offset, address, size) in enumerate(sections):
        struct.pack_into('>I', dol, index * 4, file_offset)
        struct.pack_into('>I', dol, 0x48 + index * 4, address)
        struct.pack_into('>I', dol, 0x90 + index * 4, size)
    for address, word in (
        (hooks.LOWER_HOOK, hooks.LOWER_STOCK_WORD),
        (hooks.KEY_HOOK, hooks.KEY_STOCK_WORD),
        (hooks.ROW_HOOK, hooks.ROW_STOCK_WORD),
    ):
        struct.pack_into('>I', dol, hooks._vaddr_to_file(bytes(dol), address, 4), word)
    return bytes(dol)


CUSTOM_ROWS = b''.join(
    bytes([index + 1]) * hooks.resources.RESOURCE_ROW_SIZE
    for index in range(len(ROUTES) * 2)
)


def branch_target(address, word):
    displacement = word & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    return address + displacement


class InstallRuntimeHooksTests(unittest.TestCase):
    def test_installs_hooks_stubs_state_and_rows(self):
        stock = make_dol()
        updated, changed_count, patches = hooks.patch_runtime_hooks(
            stock, stock, ROUTES, CUSTOM_ROWS
        )

        self.assertEqual(changed_count, 7)
        hooks.validate_runtime_hooks(updated, patches)
        for address, target in (
            (hooks.LOWER_HOOK, hooks.LOWER_STUB),
            (hooks.KEY_HOOK, hooks.KEY_STUB),
            (hooks.ROW_HOOK, hooks.ROW_STUB),
        ):
            offset = hooks._vaddr_to_file(updated, address, 4)
            word = struct.unpack_from('>I', updated, offset)[0]
            self.assertEqual(branch_target(address, word), target)
            self.assertEqual(word & 1, 0)
        rows_offset = hooks._vaddr_to_file(updated, hooks.CUSTOM_ROWS_ADDRESS, len(CUSTOM_ROWS))
        self.assertEqual(updated[rows_offset:rows_offset + len(CUSTOM_ROWS)], CUSTOM_ROWS)
        state_offset = hooks._vaddr_to_file(updated, hooks.STATE_ADDRESS, 8)
        self.assertEqual(updated[state_offset:state_offset + 8], bytes(8))

    def test_is_idempotent(self):
        stock = make_dol()
        updated, _, _ = hooks.patch_runtime_hooks(stock, stock, ROUTES, CUSTOM_ROWS)

        repeated, changed_count, _ = hooks.patch_runtime_hooks(
            updated, stock, ROUTES, CUSTOM_ROWS
        )

        self.assertEqual(repeated, updated)
        self.assertEqual(changed_count, 0)

    def test_rejects_modified_hook_site(self):
        stock = make_dol()
        modified = bytearray(stock)
        offset = hooks._vaddr_to_file(stock, hooks.KEY_HOOK, 4)
        struct.pack_into('>I', modified, offset, 0x60000000)

        with self.assertRaisesRegex(hooks.RuntimeHookError, 'unexpected bytes'):
            hooks.patch_runtime_hooks(bytes(modified), stock, ROUTES, CUSTOM_ROWS)

    def test_rejects_nonzero_stock_cave(self):
        stock = bytearray(make_dol())
        offset = hooks._vaddr_to_file(bytes(stock), hooks.LOWER_STUB, 1)
        stock[offset] = 1

        with self.assertRaisesRegex(hooks.RuntimeHookError, 'not zero-filled'):
            hooks.build_hook_patches(bytes(stock), ROUTES, CUSTOM_ROWS)

    def test_generated_layout_matches_guide_boundaries(self):
        self.assertEqual(hooks._custom_row_address(0, False), 0x80004E08)
        self.assertEqual(hooks._custom_row_address(5, True) + 0x14, 0x80004EF8)
        self.assertLessEqual(len(hooks._build_lower_stub(ROUTES)), 0x194)
        self.assertLessEqual(len(hooks._build_key_stub(ROUTES)), 0x1CC)
        self.assertLessEqual(len(hooks._build_row_stub(ROUTES)), 0x188)

    def test_stubs_preserve_displaced_instructions_and_returns(self):
        lower = struct.unpack(f'>{len(hooks._build_lower_stub(ROUTES)) // 4}I', hooks._build_lower_stub(ROUTES))
        key = struct.unpack(f'>{len(hooks._build_key_stub(ROUTES)) // 4}I', hooks._build_key_stub(ROUTES))
        row = struct.unpack(f'>{len(hooks._build_row_stub(ROUTES)) // 4}I', hooks._build_row_stub(ROUTES))

        self.assertEqual(lower[-2], hooks.LOWER_STOCK_WORD)
        self.assertEqual(branch_target(hooks.LOWER_STUB + (len(lower) - 1) * 4, lower[-1]), hooks.LOWER_RETURN)
        self.assertEqual(branch_target(hooks.KEY_STUB, key[0]), hooks.KEY_CALLEE)
        self.assertEqual(key[0] & 1, 1)
        self.assertEqual(branch_target(hooks.KEY_STUB + (len(key) - 1) * 4, key[-1]), hooks.KEY_RETURN)
        self.assertEqual(row[-2], hooks.ROW_STOCK_WORD)
        self.assertEqual(branch_target(hooks.ROW_STUB + (len(row) - 1) * 4, row[-1]), hooks.ROW_RETURN)


if __name__ == '__main__':
    unittest.main()