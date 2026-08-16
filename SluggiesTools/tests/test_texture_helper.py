import pathlib
import struct
import sys
import tempfile
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from texture_helper import TPL_MAGIC, parse_single_image_tpl, parse_single_image_tpl_file


def _make_direct_color_tpl() -> bytes:
    raw = bytearray(0x80)
    struct.pack_into(">III", raw, 0, TPL_MAGIC, 1, 12)
    struct.pack_into(">II", raw, 12, 0x14, 0)
    struct.pack_into(">HHII", raw, 0x14, 4, 4, 0x6, 0x40)
    raw[0x40:0x40 + 64] = b"\x11" * 64
    return bytes(raw)


def _make_indexed_tpl() -> bytes:
    raw = bytearray(0x80)
    struct.pack_into(">III", raw, 0, TPL_MAGIC, 1, 12)
    struct.pack_into(">II", raw, 12, 0x24, 0x14)
    struct.pack_into(">HHII", raw, 0x14, 2, 0, 0x2, 0x20)
    struct.pack_into(">HHII", raw, 0x24, 4, 4, 0x9, 0x40)
    raw[0x20:0x24] = b"\xAA\xBB\xCC\xDD"
    raw[0x40:0x40 + 16] = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0A\x0B\x0C\x0D\x0E\x0F\x10"
    return bytes(raw)


class TextureHelperTests(unittest.TestCase):
    def test_parse_direct_color_tpl_returns_exact_payload_bytes(self):
        tpl = _make_direct_color_tpl()

        parsed = parse_single_image_tpl(tpl)

        self.assertEqual(parsed["width"], 4)
        self.assertEqual(parsed["height"], 4)
        self.assertEqual(parsed["format"], 0x6)
        self.assertEqual(parsed["format_name"], "RGBA8")
        self.assertEqual(len(parsed["image_data"]), 64)
        self.assertEqual(parsed["image_data"], b"\x11" * 64)
        self.assertEqual(parsed["palette_data"], b"")

    def test_parse_indexed_tpl_returns_palette_and_image_bytes(self):
        tpl = _make_indexed_tpl()

        parsed = parse_single_image_tpl(tpl)

        self.assertEqual(parsed["width"], 4)
        self.assertEqual(parsed["height"], 4)
        self.assertEqual(parsed["format"], 0x9)
        self.assertEqual(parsed["format_name"], "C8")
        self.assertEqual(parsed["palette_entries"], 2)
        self.assertEqual(parsed["palette_format"], 0x2)
        self.assertEqual(parsed["palette_data"], b"\xAA\xBB\xCC\xDD")
        self.assertEqual(len(parsed["image_data"]), 32)

    def test_parse_single_image_tpl_file_reads_from_disk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tpl_path = pathlib.Path(temp_dir) / "single.tpl"
            tpl_path.write_bytes(_make_direct_color_tpl())

            parsed = parse_single_image_tpl_file(tpl_path)

            self.assertEqual(parsed["width"], 4)
            self.assertEqual(parsed["image_data"], b"\x11" * 64)

    def test_parse_rejects_truncated_and_multi_image_tpls(self):
        truncated = _make_direct_color_tpl()[:-1]
        with self.assertRaises(ValueError):
            parse_single_image_tpl(truncated)

        multi_image = bytearray(_make_direct_color_tpl())
        struct.pack_into(">III", multi_image, 0, TPL_MAGIC, 2, 12)
        with self.assertRaises(ValueError):
            parse_single_image_tpl(multi_image)


if __name__ == "__main__":
    unittest.main()
