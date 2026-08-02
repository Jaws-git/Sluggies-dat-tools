import os
import tempfile
import unittest

from PIL import Image

from SluggiesTools.Icons import add_private_texture_pages as pages
from SluggiesTools.Icons import prepare_icon_artwork as artwork
from SluggiesTools.Icons.tests.test_clone_icon_bank import make_stock_bank


class PrepareIconArtworkTests(unittest.TestCase):
    def test_load_and_harden_accepts_rgb_and_rgba(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rgb_path = os.path.join(temp_dir, 'rgb.png')
            rgba_path = os.path.join(temp_dir, 'rgba.png')
            Image.new('RGB', (48, 51), (10, 20, 30)).save(rgb_path)
            rgba = Image.new('RGBA', (48, 51), (10, 20, 30, 127))
            rgba.putpixel((1, 0), (40, 50, 60, 128))
            rgba.save(rgba_path)

            rgb = artwork.load_and_harden_image(rgb_path)
            hardened = artwork.load_and_harden_image(rgba_path)

            self.assertEqual(rgb.mode, 'RGBA')
            self.assertEqual(rgb.getpixel((0, 0)), (10, 20, 30, 255))
            self.assertEqual(hardened.getpixel((0, 0)), (0, 0, 0, 0))
            self.assertEqual(hardened.getpixel((1, 0)), (40, 50, 60, 255))

    def test_contain_resizes_and_centers_with_transparent_padding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, 'wide.png')
            Image.new('RGBA', (96, 51), (10, 20, 30, 255)).save(path)

            fitted = artwork.load_and_harden_image(path, 'contain')

            self.assertEqual(fitted.size, (48, 51))
            self.assertEqual(fitted.getpixel((24, 0)), (0, 0, 0, 0))
            self.assertEqual(fitted.getpixel((24, 25)), (10, 20, 30, 255))

    def test_cover_resizes_and_center_crops(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, 'wide.png')
            source = Image.new('RGBA', (96, 51), (255, 0, 0, 255))
            for x in range(48, 96):
                for y in range(51):
                    source.putpixel((x, y), (0, 0, 255, 255))
            source.save(path)

            fitted = artwork.load_and_harden_image(path, 'cover')

            self.assertEqual(fitted.size, (48, 51))
            self.assertEqual(fitted.getpixel((0, 25)), (255, 0, 0, 255))
            self.assertEqual(fitted.getpixel((47, 25)), (0, 0, 255, 255))

    def test_strict_rejects_wrong_dimensions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, 'large.png')
            Image.new('RGBA', (96, 102), (10, 20, 30, 255)).save(path)

            with self.assertRaisesRegex(artwork.IconArtworkError, 'expected 48x51'):
                artwork.load_and_harden_image(path, 'strict')

    def test_rejects_unknown_fit_mode(self):
        with self.assertRaisesRegex(artwork.IconArtworkError, 'unknown icon fit mode'):
            artwork.load_and_harden_image('unused.png', 'stretch')

    def test_compose_uses_eight_pixel_artwork_offset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            side_path = os.path.join(temp_dir, 'side.png')
            front_path = os.path.join(temp_dir, 'front.png')
            Image.new('RGBA', (48, 51), (255, 0, 0, 255)).save(side_path)
            Image.new('RGBA', (48, 51), (0, 255, 0, 255)).save(front_path)
            entry = artwork.ArtworkEntry('Test', 0x48, side_path, front_path, 64, 0)

            side, front = artwork.compose_atlases([entry])

            self.assertEqual(side.getpixel((71, 0)), (0, 0, 0, 0))
            self.assertEqual(side.getpixel((72, 0)), (255, 0, 0, 255))
            self.assertEqual(front.getpixel((72, 0)), (0, 255, 0, 255))

    def test_compose_rejects_slot_outside_atlas(self):
        entry = artwork.ArtworkEntry(
            'Outside', 0x48, 'unused-side.png', 'unused-front.png',
            artwork.ATLAS_WIDTH - artwork.ARTWORK_X_OFFSET, 0,
        )

        with self.assertRaisesRegex(artwork.IconArtworkError, 'does not fit inside atlas'):
            artwork.compose_atlases([entry])

    def test_extracts_structural_tpl_cmpr_payload(self):
        payload = bytes((index % 251 for index in range(pages.CMPR_IMAGE_LENGTH)))
        tpl = bytearray(0x40 + len(payload))
        import struct
        struct.pack_into('>III', tpl, 0, artwork.TPL_MAGIC, 1, 0x0C)
        struct.pack_into('>II', tpl, 0x0C, 0x14, 0)
        struct.pack_into('>HHII', tpl, 0x14, 256, 1024, pages.CMPR_FORMAT, 0x40)
        tpl[0x40:] = payload

        self.assertEqual(artwork.extract_cmpr_payload(bytes(tpl)), payload)

    def test_applies_payloads_without_changing_other_bank_bytes(self):
        stock = make_stock_bank()
        plain = stock + bytes(pages.cib.EXPANDED_BANK_LENGTH - len(stock))
        configured = pages.add_private_texture_pages(plain)
        side = bytes([0x11]) * pages.CMPR_IMAGE_LENGTH
        front = bytes([0x22]) * pages.CMPR_IMAGE_LENGTH

        updated = artwork.apply_cmpr_payloads(configured, side, front)

        self.assertEqual(
            updated[pages.SIDE_IMAGE_OFFSET:pages.SIDE_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH],
            side,
        )
        self.assertEqual(
            updated[pages.FRONT_IMAGE_OFFSET:pages.FRONT_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH],
            front,
        )
        self.assertEqual(updated[:pages.SIDE_IMAGE_OFFSET], configured[:pages.SIDE_IMAGE_OFFSET])
        self.assertEqual(updated[pages.RELOCATED_ICON_TABLE:], configured[pages.RELOCATED_ICON_TABLE:])


if __name__ == '__main__':
    unittest.main()