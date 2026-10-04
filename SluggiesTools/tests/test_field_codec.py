"""The .sluggie binary-field codec: plain base64, "z:" zlib fields, debug
byte lists, and parity between binfmt and the Blender add-on's copy."""

import base64
import pathlib
import struct
import sys
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
ADDON_DIR = TOOLS_DIR.parent / 'BlenderAddonSrc'
for import_path in (TOOLS_DIR, ADDON_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import binfmt  # noqa: E402
import FieldCodec  # noqa: E402

# Face-index-like payload: repetitive, so it compresses well.
REPETITIVE = struct.pack('>600H', *[i // 3 for i in range(600)])
TINY = b'\x01\x02\x03\x04\x05\x06\x07'


class FieldCodecTests(unittest.TestCase):
    CODECS = (('binfmt', binfmt.encode_field, binfmt.decode_field),
              ('addon', FieldCodec.encode_field, FieldCodec.decode_field))

    def test_repetitive_payload_is_compressed_and_round_trips(self):
        for name, encode, decode in self.CODECS:
            with self.subTest(name):
                value = encode(REPETITIVE, True)
                self.assertTrue(value.startswith('z:'))
                self.assertLess(len(value), len(base64.b64encode(REPETITIVE)))
                self.assertEqual(decode(value), REPETITIVE)

    def test_tiny_payload_stays_plain_base64(self):
        for name, encode, decode in self.CODECS:
            with self.subTest(name):
                value = encode(TINY, True)
                self.assertEqual(value, base64.b64encode(TINY).decode('ascii'))
                self.assertEqual(decode(value), TINY)

    def test_empty_payload(self):
        for name, encode, decode in self.CODECS:
            with self.subTest(name):
                self.assertEqual(decode(encode(b'', True)), b'')

    def test_legacy_plain_base64_still_decodes(self):
        legacy = base64.b64encode(REPETITIVE).decode('ascii')
        for name, _encode, decode in self.CODECS:
            with self.subTest(name):
                self.assertEqual(decode(legacy), REPETITIVE)

    def test_debug_byte_lists(self):
        for name, encode, decode in self.CODECS:
            with self.subTest(name):
                value = encode(TINY, False)
                self.assertEqual(value, list(TINY))
                self.assertEqual(decode(value), TINY)

    def test_none_passes_through(self):
        self.assertIsNone(binfmt.decode_field(None, True))
        self.assertIsNone(FieldCodec.decode_field(None))

    def test_binfmt_and_addon_encode_identically(self):
        for payload in (REPETITIVE, TINY, b'', bytes(range(256))):
            self.assertEqual(binfmt.encode_field(payload, True),
                             FieldCodec.encode_field(payload, True))


if __name__ == '__main__':
    unittest.main()
