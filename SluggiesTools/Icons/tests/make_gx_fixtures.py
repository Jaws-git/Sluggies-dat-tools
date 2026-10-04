"""Hand-run: write the synthetic GX texture fixtures for ``test_gx_decode`` and decode them with wimgt.

Each fixture is a single-image TPL (``<name>.tpl``) plus wimgt's decode of it
(``<name>.png``, RGBA) in ``Icons/tests/fixtures/gx``. The textures are
seeded random data, so every CMPR mode (c0 > c1 and c0 <= c1) and every
RGB5A3 branch occurs. Run once after changing the set; the files are checked
in and the test never needs wimgt.

    uv run --project SluggiesTools/_build python -m SluggiesTools.Icons.tests.make_gx_fixtures
"""

import os
import random
import struct
import subprocess
import tempfile

from PIL import Image

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'gx')
TPL_MAGIC = 0x0020AF30

# name: (format, width, height, palette format or None)
FIXTURES = {
    'cmpr_32x16': (0x0E, 32, 16, None),
    'cmpr_24x12': (0x0E, 24, 12, None),          # sides not whole tiles: padded data, cropped image
    'c8_rgb5a3_16x8': (0x09, 16, 8, 2),
    'c8_ia8_16x8': (0x09, 16, 8, 0),
    'c8_rgb565_16x8': (0x09, 16, 8, 1),
    'ia8_8x8': (0x03, 8, 8, None),
}


def _align(n: int, a: int = 0x20) -> int:
    return -(-n // a) * a


def tpl_bytes(fmt: int, width: int, height: int, image: bytes, palette: bytes = b'', palette_format=None) -> bytes:
    """A single-image TPL; the palette header (if any) at 0x14, the image header at 0x20, the data from 0x60."""
    image_at = 0x60
    palette_at = _align(image_at + len(image))
    out = bytearray(struct.pack('>III', TPL_MAGIC, 1, 0x0C))
    out += struct.pack('>II', 0x20, 0x14 if palette else 0)
    out += struct.pack('>HBBII', len(palette) // 2, 0, 0, palette_format or 0, palette_at) if palette else bytes(12)
    out += struct.pack('>HHIIIIIIfBBBB', height, width, fmt, image_at, 0, 0, 1, 1, 0.0, 0, 0, 0, 0)
    out += bytes(image_at - len(out))
    out += image
    if palette:
        out += bytes(palette_at - len(out)) + palette
    return bytes(out)


def fixture_data(name: str) -> tuple[bytes, bytes]:
    """(image data, palette) of one fixture, from a seed derived from its name."""
    fmt, width, height, palette_format = FIXTURES[name]
    rng = random.Random(name)
    if fmt == 0x0E:
        length = _align(width, 8) * _align(height, 8) // 2
        blocks = bytearray(rng.randbytes(length))
        struct.pack_into('>HH', blocks, 0, 0x1234, 0x1234)            # c0 == c1: the 3-colour + transparent mode
        struct.pack_into('>HH', blocks, 8, 0x0000, 0xFFFF)            # c0 < c1
        struct.pack_into('>HH', blocks, 16, 0xFFFF, 0x0000)           # c0 > c1
        return bytes(blocks), b''
    if fmt == 0x09:
        return rng.randbytes(width * height), rng.randbytes(512)
    return rng.randbytes(width * height * 2), b''


def main() -> int:
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    for name, (fmt, width, height, palette_format) in FIXTURES.items():
        image, palette = fixture_data(name)
        tpl = os.path.join(FIXTURE_DIR, name + '.tpl')
        with open(tpl, 'wb') as f:
            f.write(tpl_bytes(fmt, width, height, image, palette, palette_format))
        with tempfile.TemporaryDirectory() as work:
            png = os.path.join(work, 'out.png')
            subprocess.run(['wimgt', 'decode', '-q', '-o', '-d', png, tpl], check=True)
            with Image.open(png) as decoded:
                decoded.convert('RGBA').save(os.path.join(FIXTURE_DIR, name + '.png'))
        print(f'{name}: {width}x{height} format 0x{fmt:02X}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
