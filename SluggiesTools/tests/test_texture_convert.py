"""BlenderAddonSrc/TextureConvert: export-time PNG conversion of other image formats."""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT_DIR / 'BlenderAddonSrc') not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / 'BlenderAddonSrc'))
import TextureConvert  # noqa: E402  (bpy-free)


def pillow_rgba(path):
    """Stands in for TextureConvert.decode_rgba (Blender) outside Blender."""
    with Image.open(path) as img:
        rgba = img.convert('RGBA')
    return rgba.width, rgba.height, rgba.tobytes()


def sample_image(alpha=255):
    img = Image.new('RGBA', (5, 3))
    img.putdata([(x * 50, y * 80, 7, alpha if x else 255) for y in range(3) for x in range(5)])
    return img


class ConvertingTestCase(unittest.TestCase):
    """Decodes with Pillow and caches into a per-test folder."""

    def setUp(self):
        self._cache = tempfile.TemporaryDirectory()
        self.addCleanup(self._cache.cleanup)
        for patch in (mock.patch.object(TextureConvert, 'cache_dir', self._cache.name),
                      mock.patch.object(TextureConvert, 'decode_rgba', pillow_rgba)):
            patch.start()
            self.addCleanup(patch.stop)


class DetectFormatTests(unittest.TestCase):
    def test_formats_are_read_from_the_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            found = {}
            for fmt, name in (('PNG', 'a.jpg'), ('JPEG', 'b.png.bin'), ('WEBP', 'c.webp'),
                              ('BMP', 'd.bmp'), ('TGA', 'e.tga'), ('TIFF', 'f.tif'), ('GIF', 'g.gif')):
                path = os.path.join(temp_dir, name)
                sample_image().convert('RGB').save(path, fmt)
                found[name] = TextureConvert.detect_format(path)
            pathlib.Path(temp_dir, 'h.jpg').write_bytes(b'not an image at all')
            found['h.jpg'] = TextureConvert.detect_format(os.path.join(temp_dir, 'h.jpg'))
        self.assertEqual(found, {'a.jpg': 'PNG', 'b.png.bin': 'JPEG', 'c.webp': 'WEBP', 'd.bmp': 'BMP',
                                 'e.tga': 'TGA', 'f.tif': 'TIFF', 'g.gif': 'GIF', 'h.jpg': None})

    def test_tga_needs_its_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, 'e.dat')
            sample_image().save(path, 'TGA')
            self.assertIsNone(TextureConvert.detect_format(path))

    def test_convert_problem(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tiff = os.path.join(temp_dir, 'a.tif')
            sample_image().save(tiff, 'TIFF')
            junk = os.path.join(temp_dir, 'b.jpg')
            pathlib.Path(junk).write_bytes(b'jpg')
            webp = os.path.join(temp_dir, 'c.webp')
            sample_image().save(webp, 'WEBP')
            self.assertEqual(TextureConvert.convert_problem(tiff), 'is a TIFF image')
            self.assertEqual(TextureConvert.convert_problem(junk), 'is not an image the export can read')
            self.assertIsNone(TextureConvert.convert_problem(webp))
            self.assertIsNone(TextureConvert.convert_problem(os.path.join(temp_dir, 'missing.jpg')))

    def test_needs_conversion_and_converted_name(self):
        self.assertFalse(TextureConvert.needs_conversion('tex/Body.PNG'))
        self.assertTrue(TextureConvert.needs_conversion('tex/body.jpg'))
        self.assertEqual(TextureConvert.converted_name('body.final.jpeg'), 'body.final.png')


class EncodePngTests(unittest.TestCase):
    def _round_trip(self, img):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, 'out.png')
            pathlib.Path(path).write_bytes(
                TextureConvert.encode_png(img.width, img.height, img.tobytes()))
            with Image.open(path) as out:
                out.load()
                return out.mode, out.convert('RGBA').tobytes()

    def test_opaque_image_is_written_as_rgb(self):
        img = sample_image()
        self.assertEqual(self._round_trip(img), ('RGB', img.tobytes()))

    def test_alpha_is_kept_straight(self):
        img = sample_image(alpha=40)
        self.assertEqual(self._round_trip(img), ('RGBA', img.tobytes()))

    def test_same_input_gives_same_bytes(self):
        data = sample_image(alpha=0).tobytes()
        self.assertEqual(TextureConvert.encode_png(5, 3, data), TextureConvert.encode_png(5, 3, data))

    def test_wrong_byte_count_is_refused(self):
        with self.assertRaises(ValueError):
            TextureConvert.encode_png(5, 3, b'\x00' * 10)


class ConvertedPngTests(ConvertingTestCase):
    def test_conversion_is_cached_by_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, 'hat.tga')
            sample_image(alpha=0).save(source, 'TGA')
            copy = os.path.join(temp_dir, 'copy.tga')
            pathlib.Path(copy).write_bytes(pathlib.Path(source).read_bytes())
            with mock.patch.object(TextureConvert, 'decode_rgba', wraps=pillow_rgba) as decode:
                first = TextureConvert.converted_png(source)
                second = TextureConvert.converted_png(copy)
            self.assertEqual(first, second)
            self.assertEqual(decode.call_count, 1)
            self.assertEqual(TextureConvert.source_of(first), os.path.abspath(copy))
            with Image.open(first) as out:
                self.assertEqual(out.convert('RGBA').tobytes(), sample_image(alpha=0).tobytes())

    def test_unsupported_format_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, 'hat.tif')
            sample_image().save(source, 'TIFF')
            with self.assertRaisesRegex(ValueError, r'hat\.tif is a TIFF image'):
                TextureConvert.converted_png(source)


if __name__ == '__main__':
    unittest.main()
