import os
import pathlib
import struct
import sys
import tempfile
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from texture_helper import (
    TPL_MAGIC,
    TextureEncodingError,
    encode_png_to_tpl,
    parse_single_image_tpl,
    parse_single_image_tpl_file,
    wimgt_target_for,
)


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


class WimgtTargetMappingTests(unittest.TestCase):
    def test_direct_color_formats_map_to_explicit_targets(self):
        self.assertEqual(wimgt_target_for(0x0), "TPL.I4")
        self.assertEqual(wimgt_target_for(0x1), "TPL.I8")
        self.assertEqual(wimgt_target_for(0x2), "TPL.IA4")
        self.assertEqual(wimgt_target_for(0x3), "TPL.IA8")
        self.assertEqual(wimgt_target_for(0x4), "TPL.RGB565")
        self.assertEqual(wimgt_target_for(0x5), "TPL.RGB5A3")
        self.assertEqual(wimgt_target_for(0x6), "TPL.RGBA8")
        self.assertEqual(wimgt_target_for(0xE), "TPL.CMPR")

    def test_indexed_formats_require_a_supported_palette(self):
        self.assertEqual(wimgt_target_for(0x8, 0x0), "TPL.C4.P-IA8")
        self.assertEqual(wimgt_target_for(0x9, 0x0), "TPL.C8.P-IA8")
        self.assertEqual(wimgt_target_for(0x9, 0x1), "TPL.C8.P-RGB565")
        self.assertEqual(wimgt_target_for(0x9, 0x2), "TPL.C8.P-RGB5A3")
        self.assertEqual(wimgt_target_for(0xA, 0x2), "TPL.C14X2.P-RGB5A3")

    def test_indexed_format_without_palette_is_rejected(self):
        with self.assertRaises(ValueError):
            wimgt_target_for(0x9)
        with self.assertRaises(ValueError):
            wimgt_target_for(0x9, 0x7)

    def test_unsupported_format_is_rejected(self):
        with self.assertRaises(ValueError):
            wimgt_target_for(0x7)
        with self.assertRaises(ValueError):
            wimgt_target_for(0xF)


def _wimgt_available() -> bool:
    import shutil
    return shutil.which("wimgt") is not None


@unittest.skipUnless(_wimgt_available(), "wimgt not on PATH")
class EncodePngToTplTests(unittest.TestCase):
    def _make_fixture_png(self, temp_dir: str, name: str = "fixture.png") -> str:
        from PIL import Image
        path = os.path.join(temp_dir, name)
        img = Image.new("RGBA", (8, 8), (200, 100, 50, 255))
        img.save(path, "PNG")
        return path

    def test_encode_direct_color_rgba8(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            png = self._make_fixture_png(temp_dir)
            parsed = encode_png_to_tpl(png, gx_format=0x6)
            self.assertEqual(parsed["width"], 8)
            self.assertEqual(parsed["height"], 8)
            self.assertEqual(parsed["format"], 0x6)
            self.assertEqual(parsed["format_name"], "RGBA8")
            # 8x8 RGBA8: ceil(8/4) * ceil(8/4) * 64 = 256 bytes
            self.assertEqual(len(parsed["image_data"]), 256)
            self.assertEqual(parsed["palette_data"], b"")

    def test_encode_cmpr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            png = self._make_fixture_png(temp_dir)
            parsed = encode_png_to_tpl(png, gx_format=0xE)
            self.assertEqual(parsed["width"], 8)
            self.assertEqual(parsed["height"], 8)
            self.assertEqual(parsed["format"], 0xE)
            self.assertEqual(parsed["format_name"], "CMPR")
            self.assertEqual(len(parsed["image_data"]), 32)
            self.assertEqual(parsed["palette_data"], b"")

    def test_encode_indexed_c8_with_rgb5a3_palette(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            png = self._make_fixture_png(temp_dir)
            parsed = encode_png_to_tpl(png, gx_format=0x9, palette_format=0x2)
            self.assertEqual(parsed["width"], 8)
            self.assertEqual(parsed["height"], 8)
            self.assertEqual(parsed["format"], 0x9)
            self.assertEqual(parsed["format_name"], "C8")
            self.assertEqual(parsed["palette_format"], 0x2)
            self.assertGreater(parsed["palette_entries"], 0)
            self.assertEqual(len(parsed["palette_data"]), parsed["palette_entries"] * 2)
            self.assertEqual(len(parsed["image_data"]), 64)

class EncodePngToTplErrorTests(unittest.TestCase):
    def test_missing_wimgt_raises_texture_encoding_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from PIL import Image
            png = os.path.join(temp_dir, "fixture.png")
            Image.new("RGBA", (8, 8), (200, 100, 50, 255)).save(png, "PNG")
            with self.assertRaises(TextureEncodingError) as ctx:
                encode_png_to_tpl(png, gx_format=0x6, wimgt_executable="definitely_not_a_real_wimgt_exe")
            self.assertEqual(ctx.exception.exit_code, None)
            self.assertIn("wimgt executable not found", str(ctx.exception))

    def test_unsupported_format_raises_value_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from PIL import Image
            png = os.path.join(temp_dir, "fixture.png")
            Image.new("RGBA", (8, 8), (200, 100, 50, 255)).save(png, "PNG")
            with self.assertRaises(ValueError):
                encode_png_to_tpl(png, gx_format=0x7)


if __name__ == "__main__":
    unittest.main()
