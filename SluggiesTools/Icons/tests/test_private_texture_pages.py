import os
import struct
import tempfile
import unittest
from unittest.mock import patch

from SluggiesTools.Icons import add_private_texture_pages as pages
from SluggiesTools.Icons.tests.test_clone_icon_bank import make_stock_bank


class PrivateTexturePageTests(unittest.TestCase):
    def setUp(self):
        stock = make_stock_bank()
        self.stock_icon_table = stock[pages.cib.STOCK_ICON_TABLE:]
        self.plain_clone = stock + bytes(pages.cib.EXPANDED_BANK_LENGTH - len(stock))

    def test_adds_private_descriptors_and_relocates_icon_table(self):
        configured = pages.add_private_texture_pages(self.plain_clone)

        self.assertEqual(len(configured), pages.cib.EXPANDED_BANK_LENGTH)
        self.assertEqual(struct.unpack_from('>I', configured, 0x04)[0], pages.RELOCATED_ICON_TABLE)
        self.assertEqual(struct.unpack_from('>H', configured, 0x20)[0], pages.PRIVATE_TEXTURE_COUNT)
        self.assertEqual(
            configured[
                pages.RELOCATED_ICON_TABLE:pages.RELOCATED_ICON_TABLE + len(self.stock_icon_table)
            ],
            self.stock_icon_table,
        )
        pages.validate_private_texture_pages(configured, self.stock_icon_table)

    def test_private_payloads_are_blank_and_palette_free(self):
        configured = pages.add_private_texture_pages(self.plain_clone)

        for descriptor_offset, image_offset in (
            (pages.SIDE_DESCRIPTOR_OFFSET, pages.SIDE_IMAGE_OFFSET),
            (pages.FRONT_DESCRIPTOR_OFFSET, pages.FRONT_IMAGE_OFFSET),
        ):
            descriptor = configured[descriptor_offset:descriptor_offset + pages.DESCRIPTOR_SIZE]
            self.assertEqual(struct.unpack_from('>I', descriptor, 0)[0], image_offset)
            self.assertEqual(struct.unpack_from('>I', descriptor, 4)[0], 0)
            self.assertEqual(struct.unpack_from('>HH', descriptor, 8), (256, 1024))
            self.assertEqual(descriptor[0x17], pages.CMPR_FORMAT)
            self.assertEqual(descriptor[0x18:0x1B], b'\x00\x00\x00')
            self.assertEqual(
                configured[image_offset:image_offset + pages.CMPR_IMAGE_LENGTH],
                bytes(pages.CMPR_IMAGE_LENGTH),
            )

    def test_rejects_wrong_source_layout(self):
        wrong = bytearray(self.plain_clone)
        struct.pack_into('>H', wrong, 0x20, pages.PRIVATE_TEXTURE_COUNT)

        with self.assertRaisesRegex(pages.PrivateTexturePageError, 'not a plain Step 1 clone'):
            pages.add_private_texture_pages(bytes(wrong))

    def test_installer_upgrades_existing_step_1_clone_in_place(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = os.path.join(temp_dir, 'input.dat')
            input_dol = os.path.join(temp_dir, 'input.dol')
            output_dat = os.path.join(temp_dir, 'output.dat')
            output_dol = os.path.join(temp_dir, 'output.dol')
            destination = 0x1000

            with open(input_dat, 'wb') as dat:
                dat.seek(pages.cib.STOCK_BANK_OFFSET)
                dat.write(make_stock_bank())
            from SluggiesTools.Icons.tests.test_clone_icon_bank import write_dol
            write_dol(input_dol)
            write_dol(output_dol, destination, pages.cib.EXPANDED_BANK_LENGTH)
            with open(output_dat, 'wb') as dat:
                dat.seek(destination)
                dat.write(self.plain_clone)

            with (
                patch.object(pages.cib, 'INPUT_DAT', input_dat),
                patch.object(pages.cib, 'INPUT_DOL', input_dol),
                patch.object(pages.cib, 'OUTPUT_DAT', output_dat),
                patch.object(pages.cib, 'OUTPUT_DOL', output_dol),
                patch.object(pages.cib.hh, 'patchFstFileSize', return_value=True),
            ):
                result = pages.install_private_texture_pages()

            self.assertTrue(result.upgraded_in_place)
            self.assertEqual(result.destination_offset, destination)
            with open(output_dat, 'rb') as dat:
                dat.seek(destination)
                configured = dat.read(pages.cib.EXPANDED_BANK_LENGTH)
            pages.validate_private_texture_pages(configured, self.stock_icon_table)

    def test_configured_pages_may_contain_later_artwork(self):
        configured = bytearray(pages.add_private_texture_pages(self.plain_clone))
        configured[pages.SIDE_IMAGE_OFFSET] = 1

        pages.validate_private_texture_pages(bytes(configured), require_blank=False)


if __name__ == '__main__':
    unittest.main()