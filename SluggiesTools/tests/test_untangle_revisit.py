"""--untangle changes a texture once, however often its block is visited (one
block reached from several directory records)."""
import io
import pathlib
import sys
import unittest
from types import SimpleNamespace

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import texture_export  # noqa: E402

BLOCK = 0x100
IMAGE = 0x20


class FakeTex:
    """Hashes its name from the bytes now in the shared DAT, like a re-parsed block."""

    def __init__(self, dat):
        self.dat = dat
        self.parent = SimpleNamespace(absolute=BLOCK)
        self.dataPtr = IMAGE
        self.paletteDataPtr = 0

    def _bytes(self):
        return self.dat.getvalue()[BLOCK + IMAGE:BLOCK + IMAGE + 4]

    def name(self):
        return 'tex_' + self._bytes().hex()

    def ensureUniqueDolphinBasename(self, seen_names, max_attempts=8192):
        data = self._bytes()
        original = 'tex_' + data.hex()
        if original not in seen_names:
            seen_names.add(original)
            return {'basename': original, 'image_data': data, 'tlut_data': b'', 'changed': False,
                    'attempts': 0, 'warning': None, 'original_basename': original}
        mutated = bytes([data[0] ^ 0x11]) + data[1:]
        seen_names.add('tex_' + mutated.hex())
        return {'basename': 'tex_' + mutated.hex(), 'image_data': mutated, 'tlut_data': b'', 'changed': True,
                'attempts': 1, 'warning': None, 'original_basename': original}


class UntangleRevisitTests(unittest.TestCase):
    def test_a_shared_block_is_changed_once(self):
        dat = io.BytesIO(bytes(0x200))
        dat.seek(BLOCK + IMAGE)
        dat.write(b'\x01\x02\x03\x04')
        tex = FakeTex(dat)
        taken = tex.name()                          # the same texture name in another block
        context = texture_export.UntangleContext(seen_names={taken}, dat_output_handle=dat)
        names = []
        for visit in range(4):
            name, image_override, _tlut = texture_export.untangle_texture(
                context, tex, tex.name(), BLOCK + IMAGE, BLOCK, 0)
            names.append(name)
            self.assertEqual(image_override, b'\x10\x02\x03\x04')
        self.assertEqual(len(set(names)), 1)
        self.assertNotEqual(names[0], taken)
        self.assertEqual(tex._bytes(), b'\x10\x02\x03\x04')   # one byte flipped once, not on every visit
        self.assertEqual(sum('untangled:' in line for line in context.report_lines), 1)

    def test_ignored_names_are_left_alone(self):
        name = next(iter(texture_export.UNTANGLE_IGNORE_BASENAME))
        tex = FakeTex(io.BytesIO(bytes(0x200)))
        result = texture_export.untangle_texture(texture_export.UntangleContext(), tex, name, BLOCK + IMAGE, BLOCK, 0)
        self.assertEqual(result, (name, None, None))


if __name__ == '__main__':
    unittest.main()
