"""The .sluggie codec: "x:" hex fields, legacy base64 and "z:" zlib fields,
debug byte lists, xz-compressed and plain files, and parity between binfmt
and the Blender add-on's copy."""

import base64
import json
import lzma
import pathlib
import struct
import sys
import tempfile
import unittest
import zlib

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


def _document(use_compression, payload=REPETITIVE):
    return {'SluggiesModel': {'ChunkNumber': 25, 'FileIndex': 3, 'UseCompression': use_compression,
                              'Field': binfmt.encode_field(payload, use_compression)}}


class FieldCodecTests(unittest.TestCase):
    CODECS = (('binfmt', binfmt.encode_field, binfmt.decode_field),
              ('addon', FieldCodec.encode_field, FieldCodec.decode_field))

    def test_fields_are_prefixed_hex_and_round_trip(self):
        for name, encode, decode in self.CODECS:
            for payload in (REPETITIVE, TINY, b'', bytes(range(256))):
                with self.subTest(name, size=len(payload)):
                    value = encode(payload, True)
                    self.assertEqual(value, 'x:' + payload.hex())
                    self.assertEqual(decode(value), payload)

    def test_legacy_plain_base64_still_decodes(self):
        legacy = base64.b64encode(REPETITIVE).decode('ascii')
        for name, _encode, decode in self.CODECS:
            with self.subTest(name):
                self.assertEqual(decode(legacy), REPETITIVE)

    def test_legacy_zlib_field_still_decodes(self):
        legacy = 'z:' + base64.b64encode(zlib.compress(REPETITIVE)).decode('ascii')
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

    def test_addon_compact_form_decodes(self):
        value = FieldCodec.encode_compact(REPETITIVE)
        self.assertTrue(value.startswith('z:'))
        self.assertEqual(FieldCodec.decode_field(value), REPETITIVE)


class SluggieFileTests(unittest.TestCase):
    IO = (('binfmt', binfmt.dump_sluggie, binfmt.load_sluggie),
          ('addon', FieldCodec.dump_sluggie, FieldCodec.load_sluggie))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._tmp.name) / 'model.sluggie'

    def tearDown(self):
        self._tmp.cleanup()

    def test_regular_file_is_xz_and_round_trips(self):
        for name, dump, load in self.IO:
            with self.subTest(name):
                document = _document(True)
                dump(document, self.path)
                raw = self.path.read_bytes()
                self.assertTrue(raw.startswith(binfmt.XZ_MAGIC))
                self.assertEqual(json.loads(lzma.decompress(raw)), document)
                self.assertEqual(load(self.path), document)

    def test_debug_file_is_plain_json_with_byte_lists(self):
        for name, dump, load in self.IO:
            with self.subTest(name):
                document = _document(False, TINY)
                dump(document, self.path)
                text = self.path.read_text(encoding='utf-8')
                self.assertEqual(json.loads(text), document)
                self.assertEqual(json.loads(text)['SluggiesModel']['Field'], list(TINY))
                self.assertEqual(load(self.path), document)

    def test_given_text_is_written(self):
        document = _document(False)
        binfmt.dump_sluggie(document, self.path, text='{"SluggiesModel": {"UseCompression": false}}')
        self.assertEqual(binfmt.load_sluggie(self.path), {'SluggiesModel': {'UseCompression': False}})

    def test_legacy_plain_json_file_loads(self):
        legacy = {'SluggiesModel': {'UseCompression': True,
                                    'Field': base64.b64encode(REPETITIVE).decode('ascii')}}
        self.path.write_text(json.dumps(legacy, indent=2), encoding='utf-8')
        for name, _dump, load in self.IO:
            with self.subTest(name):
                self.assertEqual(load(self.path), legacy)

    def test_damaged_xz_raises_json_decode_error(self):
        self.path.write_bytes(binfmt.XZ_MAGIC + b'not an xz stream')
        for name, _dump, load in self.IO:
            with self.subTest(name), self.assertRaises(json.JSONDecodeError):
                load(self.path)

    def test_head_reads_leading_text_of_both_forms(self):
        for use_compression in (True, False):
            with self.subTest(use_compression=use_compression):
                binfmt.dump_sluggie(_document(use_compression), self.path)
                head = binfmt.read_sluggie_head(self.path, 64)
                self.assertEqual(len(head), 64)
                self.assertTrue(head.startswith('{\n  "SluggiesModel": {\n    "ChunkNumber": 25'))

    def test_binfmt_and_addon_write_identical_files(self):
        other = self.path.with_name('addon.sluggie')
        for use_compression in (True, False):
            with self.subTest(use_compression=use_compression):
                document = _document(use_compression)
                binfmt.dump_sluggie(document, self.path)
                FieldCodec.dump_sluggie(document, other)
                self.assertEqual(self.path.read_bytes(), other.read_bytes())


if __name__ == '__main__':
    unittest.main()
