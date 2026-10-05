"""The numpy GX decoder matches wimgt's decode of synthetic textures.

The fixtures (``fixtures/gx``) were written once by the hand-run
``make_gx_fixtures.py``: a TPL per texture and wimgt's RGBA decode of it.
"""

import os
import struct
import unittest

import numpy as np
from PIL import Image

from SluggiesTools.Icons import gx_decode
from SluggiesTools.Icons.tests import make_gx_fixtures as fx


def read_tpl(path: str):
    """(format, width, height, image data, palette, palette format) of a fixture TPL."""
    with open(path, 'rb') as f:
        tpl = f.read()
    image_header, palette_header = struct.unpack_from('>II', tpl, 0x0C)
    height, width, fmt, image_at = struct.unpack_from('>HHII', tpl, image_header)
    palette, palette_format = b'', 0
    if palette_header:
        entries, _u, _p, palette_format, palette_at = struct.unpack_from('>HBBII', tpl, palette_header)
        palette = tpl[palette_at:palette_at + 2 * entries]
    image = tpl[image_at:image_at + gx_decode.data_length(fmt, width, height)]
    return fmt, width, height, image, palette, palette_format


class WimgtMatchTests(unittest.TestCase):
    def test_every_fixture_matches_wimgt(self):
        for name in fx.FIXTURES:
            with self.subTest(name):
                fmt, width, height, image, palette, palette_format = read_tpl(
                    os.path.join(fx.FIXTURE_DIR, name + '.tpl'))
                with Image.open(os.path.join(fx.FIXTURE_DIR, name + '.png')) as png:
                    expected = np.asarray(png.convert('RGBA'))
                got = gx_decode.decode(fmt, image, width, height, palette, palette_format)
                self.assertEqual(got.shape, (height, width, 4))
                if fmt == gx_decode.CMPR:
                    # wimgt writes transparent texels' colour as whatever it computed; only alpha matters there
                    got, expected = got.copy(), expected.copy()
                    got[got[..., 3] == 0] = 0
                    expected[expected[..., 3] == 0] = 0
                np.testing.assert_array_equal(got, expected)

    def test_fixtures_are_the_generator_data(self):
        """The checked-in TPLs still hold what ``make_gx_fixtures`` would write (a stale set fails here)."""
        for name, (fmt, width, height, palette_format) in fx.FIXTURES.items():
            with self.subTest(name):
                image, palette = fx.fixture_data(name)
                with open(os.path.join(fx.FIXTURE_DIR, name + '.tpl'), 'rb') as f:
                    self.assertEqual(f.read(), fx.tpl_bytes(fmt, width, height, image, palette, palette_format))


class DecoderTests(unittest.TestCase):
    def test_c8_palette_index_beyond_palette_is_refused(self):
        with self.assertRaises(gx_decode.GxDecodeError):
            gx_decode.decode(gx_decode.C8, bytes([5]) * 32, 8, 4, bytes(4), gx_decode.PALETTE_RGB5A3)

    def test_short_data_is_refused(self):
        with self.assertRaises(gx_decode.GxDecodeError):
            gx_decode.decode(gx_decode.CMPR, bytes(31), 8, 8)

    def test_unknown_format_is_refused(self):
        with self.assertRaises(gx_decode.GxDecodeError):
            gx_decode.decode(0x06, bytes(256), 8, 8)

    def test_cmpr_solid_block(self):
        block = struct.pack('>HHI', 0xF800, 0x0000, 0) * 4         # red, all indices 0
        got = gx_decode.decode(gx_decode.CMPR, block, 8, 8)
        self.assertTrue((got == np.array([255, 0, 0, 255], np.uint8)).all())

    def test_c8_tiles_are_8x4(self):
        data = bytes(range(64))                                     # 16x4: two 8x4 tiles
        palette = b''.join(struct.pack('>H', 0x8000 | (i >> 5) << 10 | i & 31) for i in range(64))   # RGB555
        got = gx_decode.decode(gx_decode.C8, data, 16, 4, palette, gx_decode.PALETTE_RGB5A3)
        index = (got[..., 0] >> 3).astype(int) * 32 + (got[..., 2] >> 3)
        self.assertEqual(index[0, :8].tolist(), list(range(8)))
        self.assertEqual(index[0, 8:].tolist(), list(range(32, 40)))
        self.assertEqual(index[1, :8].tolist(), list(range(8, 16)))


if __name__ == '__main__':
    unittest.main()
