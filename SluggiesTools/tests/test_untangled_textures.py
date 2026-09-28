import json
import pathlib
import struct
import sys
import tempfile
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import UntangledTextures as ut
from tpl import TEXDescriptor

WIDTH = HEIGHT = 8        # one 32-byte CMPR tile
CMPR = 14
PAYLOAD = bytes(range(32))


def model_block(payload=PAYLOAD) -> bytearray:
    """A model header whose TEX (at +0x20) holds one 8x8 CMPR texture."""
    block = bytearray(0x80)
    struct.pack_into('>I', block, 0x0C, 0x20)             # texPtr
    struct.pack_into('>HH', block, 0x20, 1, 0)            # count, clut
    struct.pack_into('>II', block, 0x24, 0x40, 0)         # dataPtr (TEX-relative), palette
    struct.pack_into('>HH', block, 0x2C, HEIGHT, WIDTH)
    block[0x24 + 0x17] = CMPR
    block[0x60:0x80] = payload
    return block


class _Parent:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def seek(self, pos):
        self.pos = pos

    def read(self, length):
        return self.data[self.pos:self.pos + length]


def export_untangled(payload, taken_attempts: int):
    """What export.py --untangle writes when ``taken_attempts`` earlier names are taken."""
    descriptor = TEXDescriptor.__new__(TEXDescriptor)
    descriptor.width, descriptor.height, descriptor.format = WIDTH, HEIGHT, CMPR
    descriptor.dataPtr, descriptor.paletteDataPtr, descriptor.paletteEntries = 0, 0, 0
    descriptor.parent = _Parent(payload)
    seen = set()
    for _ in range(taken_attempts + 1):
        result = descriptor.ensureUniqueDolphinBasename(seen)
    return result


class ReplayTests(unittest.TestCase):
    def test_reapply_reproduces_the_exports_untangled_bytes(self):
        for attempts in (1, 3):
            with self.subTest(attempts=attempts):
                expected = export_untangled(PAYLOAD, attempts)
                self.assertTrue(expected['changed'])
                block, vanilla = model_block(), model_block()
                notes = ut.reapply(block, 0, [{'TextureIndex': 0, 'TextureFileName': expected['basename'] + '.png'}],
                                   vanilla, 0)
                self.assertEqual(bytes(block[0x60:0x80]), expected['image_data'])
                self.assertIn('re-applied', notes[0])

    def test_texture_already_named_as_vanilla_is_left_alone(self):
        name = export_untangled(PAYLOAD, 0)['basename']
        block = model_block()
        self.assertEqual(ut.reapply(block, 0, [{'TextureIndex': 0, 'TextureFileName': name + '.png'}],
                                    model_block(), 0), [])
        self.assertEqual(block, model_block())

    def test_edited_texture_is_not_replayed(self):
        target = export_untangled(PAYLOAD, 1)['basename']
        block = model_block(b'\xEE' * 32)
        self.assertEqual(ut.reapply(block, 0, [{'TextureIndex': 0, 'TextureFileName': target + '.png'}],
                                    model_block(), 0), [])
        self.assertEqual(bytes(block[0x60:0x80]), b'\xEE' * 32)

    def test_unreachable_name_keeps_vanilla_and_says_so(self):
        block = model_block()
        notes = ut.reapply(block, 0, [{'TextureIndex': 0, 'TextureFileName': 'tex1_8x8_0000000000000000_14.png'}],
                           model_block(), 0)
        self.assertEqual(block, model_block())
        self.assertIn('kept vanilla bytes', notes[0])


class FindSluggieModelTests(unittest.TestCase):
    def test_finds_the_route_inside_its_dir_folder_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            for folder, chunk, index in (('89 Unused Yoshi A/1/a', 89, 2), ('89 Unused Yoshi A/1/b', 89, 3),
                                         ('24 Yoshi/1/a', 89, 2)):
                path = root / folder / 'm.sluggie'
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'SluggiesModel': {
                    'ChunkNumber': chunk, 'FileIndex': index, 'Tag': folder}}), encoding='utf-8')

            model = ut.find_sluggie_model(89, 2, str(root))
            self.assertEqual(model['Tag'], '89 Unused Yoshi A/1/a')
            self.assertIsNone(ut.find_sluggie_model(89, 9, str(root)))


if __name__ == '__main__':
    unittest.main()
