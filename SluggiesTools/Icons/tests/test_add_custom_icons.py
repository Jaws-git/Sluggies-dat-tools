import struct
import unittest

from SluggiesTools.Icons import add_custom_icons as custom
from SluggiesTools.Icons import add_private_texture_pages as pages
from SluggiesTools.Icons import prepare_icon_artwork as artwork
from SluggiesTools.Icons.tests.test_update_icon_source_tables import (
    ROUTES,
    make_configured_bank,
    make_plain_source_bank,
)
from SluggiesTools.Icons.tests.test_install_runtime_hooks import CUSTOM_ROWS, make_dol


ENTRIES = [
    artwork.ArtworkEntry(
        f'Character {index}',
        char_id,
        '',
        '',
        index * artwork.SLOT_X_STRIDE,
        0,
    )
    for index, char_id in enumerate((0x48, 0x49, 0x4A, 0x4B, 0x47, 0x4C))
]


def make_stage_dol():
    dol = bytearray(make_dol())
    required_size = custom.cib.ICON_ENTRY_DOL_OFFSET + custom.cib.ICON_ENTRY_SIZE
    dol.extend(bytes(required_size - len(dol)))
    words = []
    for filename_pointer in (0x10, 0x20, 0x30):
        words.extend((
            filename_pointer,
            custom.cib.STOCK_BANK_LENGTH,
            custom.cib.STOCK_BANK_OFFSET,
            custom.cib.STOCK_BANK_LENGTH,
        ))
    struct.pack_into('>12I', dol, custom.cib.ICON_ENTRY_DOL_OFFSET, *words)
    return bytes(dol)


class AddCustomIconsTests(unittest.TestCase):
    def test_build_final_bank_composes_steps_two_through_five(self):
        plain = make_plain_source_bank()
        side = bytes([0x31]) * pages.CMPR_IMAGE_LENGTH
        front = bytes([0x42]) * pages.CMPR_IMAGE_LENGTH

        final = custom.build_final_bank(plain, ROUTES, ENTRIES, side, front)

        custom.resources.validate_icon_resource_rows(final, ROUTES, ENTRIES)
        self.assertEqual(
            final[pages.SIDE_IMAGE_OFFSET:pages.SIDE_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH],
            side,
        )
        self.assertEqual(
            final[pages.FRONT_IMAGE_OFFSET:pages.FRONT_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH],
            front,
        )

    def test_build_final_bank_updates_artwork_idempotently(self):
        source_bank = custom.sources.update_icon_source_tables(make_configured_bank(), ROUTES)
        source_bank = custom.resources.add_icon_resource_rows(source_bank, ROUTES, ENTRIES)
        side = bytes([0x55]) * pages.CMPR_IMAGE_LENGTH
        front = bytes([0x66]) * pages.CMPR_IMAGE_LENGTH

        first = custom.build_final_bank(source_bank, ROUTES, ENTRIES, side, front)
        repeated = custom.build_final_bank(first, ROUTES, ENTRIES, side, front)

        self.assertEqual(repeated, first)

    def test_diagnostic_bank_stages_are_cumulative(self):
        plain = make_plain_source_bank()
        side = bytes([0x31]) * pages.CMPR_IMAGE_LENGTH
        front = bytes([0x42]) * pages.CMPR_IMAGE_LENGTH
        private_pages = pages.add_private_texture_pages(plain)
        with_artwork = artwork.apply_cmpr_payloads(private_pages, side, front)
        relocated_sources = custom.sources.relocate_icon_source_tables(with_artwork)
        with_resources = custom.resources.add_icon_resource_rows(
            relocated_sources, ROUTES, ENTRIES
        )
        with_sources = custom.sources.append_custom_source_records(
            with_resources, ROUTES
        )
        expected_by_stage = {
            'a': plain,
            'b': private_pages,
            'c': with_artwork,
            'd': relocated_sources,
            'e': with_resources,
            'f': with_sources,
        }

        for stage, expected in expected_by_stage.items():
            actual = custom.build_diagnostic_bank(
                plain, ROUTES, ENTRIES, side, front, stage
            )
            self.assertEqual(actual, expected)

        final = custom.build_final_bank(plain, ROUTES, ENTRIES, side, front)
        self.assertEqual(expected_by_stage['f'], final)

    def test_patch_icon_entry_updates_all_language_slots(self):
        dol = bytearray(custom.cib.ICON_ENTRY_DOL_OFFSET + custom.cib.ICON_ENTRY_SIZE)
        filename_pointers = (0x10, 0x20, 0x30)
        words = []
        for pointer in filename_pointers:
            words.extend((pointer, custom.cib.STOCK_BANK_LENGTH, custom.cib.STOCK_BANK_OFFSET, custom.cib.STOCK_BANK_LENGTH))
        struct.pack_into('>12I', dol, custom.cib.ICON_ENTRY_DOL_OFFSET, *words)

        updated = custom._patch_icon_entry_bytes(bytes(dol), 0x2BF81E20)
        entry = custom._read_icon_entry_bytes(updated)

        self.assertEqual(entry.filename_pointers, filename_pointers)
        self.assertEqual(entry.offset, 0x2BF81E20)
        self.assertEqual(entry.length, custom.cib.EXPANDED_BANK_LENGTH)
        self.assertEqual(entry.allocation, custom.cib.EXPANDED_BANK_LENGTH)

    def test_rejects_mismatched_description_sets(self):
        with self.assertRaisesRegex(custom.CustomIconInstallError, 'artwork character IDs differ'):
            custom._validate_description_sets(ROUTES, ENTRIES[:-1], [])

    def test_diagnostic_stages_activate_hook_regions_cumulatively(self):
        stock = make_stage_dol()
        destination = 0x2BF81E20
        patch_names_by_stage = {
            'a': set(),
            'b': set(),
            'c': set(),
            'd': set(),
            'e': set(),
            'f': set(),
            'g': set(),
            'h': {'lower stub', 'state and custom rows', 'key stub', 'row stub'},
            'i': {'lower stub', 'state and custom rows', 'key stub', 'row stub', 'lower hook'},
            'j': {'lower stub', 'state and custom rows', 'key stub', 'row stub', 'lower hook', 'key hook'},
            'k': {'lower stub', 'state and custom rows', 'key stub', 'row stub', 'lower hook', 'key hook', 'row hook'},
        }

        for stage, expected_names in patch_names_by_stage.items():
            staged, _, patches = custom.patch_diagnostic_dol(
                stock, destination, ROUTES, [], CUSTOM_ROWS, stage
            )
            for patch in patches:
                offset = custom.hooks._vaddr_to_file(staged, patch.address, len(patch.patched))
                expected = patch.patched if patch.name in expected_names else patch.stock
                self.assertEqual(staged[offset:offset + len(expected)], expected)

        full, _, _ = custom.patch_final_dol(
            stock, stock, destination, ROUTES, [], CUSTOM_ROWS
        )
        stage_k, _, _ = custom.patch_diagnostic_dol(
            stock, destination, ROUTES, [], CUSTOM_ROWS, 'k'
        )
        self.assertEqual(stage_k, full)

    def test_removes_exact_legacy_four_character_hooks(self):
        stock = make_stage_dol()
        legacy_routes = ROUTES[:custom.LEGACY_FOUR_COUNT]
        legacy_rows = CUSTOM_ROWS[:
            custom.LEGACY_FOUR_COUNT * custom.hooks.CUSTOM_ROW_STRIDE
        ]
        legacy_dol, _, _ = custom.hooks.patch_runtime_hooks(
            stock, stock, legacy_routes, legacy_rows
        )

        restored = custom._remove_legacy_four_hooks(
            legacy_dol, stock, ROUTES
        )

        self.assertEqual(restored, stock)


if __name__ == '__main__':
    unittest.main()
