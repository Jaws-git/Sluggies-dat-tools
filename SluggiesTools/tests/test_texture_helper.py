import contextlib
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
    SkippedTexture,
    TextureEncodingError,
    TexturePlan,
    TexturePlanEntry,
    TextureWrite,
    build_texture_plan,
    build_texture_writes,
    texture_writes_for,
    check_png_dimensions,
    encode_png_to_tpl,
    parse_single_image_tpl,
    parse_single_image_tpl_file,
    read_png_dimensions,
    resolve_tex_dir,
    resolve_texture_path,
    validate_texture_descriptors,
    validate_texture_file_name,
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

    def test_parse_rejects_descriptors_inside_image_table(self):
        malformed = bytearray(_make_direct_color_tpl())
        struct.pack_into(">I", malformed, 12, 12)

        with self.assertRaisesRegex(ValueError, "image descriptor"):
            parse_single_image_tpl(malformed)

    def test_parse_rejects_unexpected_direct_color_palette(self):
        malformed = bytearray(_make_direct_color_tpl()) + bytearray(8)
        struct.pack_into(">II", malformed, 12, 0x14, 0x24)
        struct.pack_into(">HHII", malformed, 0x24, 2, 0, 0x2, 0x80)

        with self.assertRaisesRegex(ValueError, "unexpected palette"):
            parse_single_image_tpl(malformed)

    def test_parse_rejects_palette_payload_overlap_in_either_direction(self):
        malformed = bytearray(_make_indexed_tpl())
        struct.pack_into(">I", malformed, 0x14 + 8, 0x50)

        with self.assertRaisesRegex(ValueError, "payload ranges overlap"):
            parse_single_image_tpl(malformed)


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


@contextlib.contextmanager
def _chdir(path: str):
    """Temporarily change the process working directory, restoring it on exit."""
    original = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(original)


class ResolveTexDirTests(unittest.TestCase):
    def test_absolute_sluggie_path_resolves_tex_as_sibling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = os.path.join(temp_dir, "model")
            os.makedirs(model_dir)
            sluggie = os.path.join(model_dir, "model.sluggie")

            tex_dir = resolve_tex_dir(sluggie)

            self.assertEqual(tex_dir, os.path.join(model_dir, "tex"))

    def test_relative_sluggie_path_resolves_against_cwd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = os.path.join(temp_dir, "model")
            os.makedirs(model_dir)

            with _chdir(temp_dir):
                tex_dir = resolve_tex_dir(os.path.join("model", "model.sluggie"))

            self.assertEqual(tex_dir, os.path.join(model_dir, "tex"))

    def test_bare_filename_resolves_against_cwd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with _chdir(temp_dir):
                tex_dir = resolve_tex_dir("model.sluggie")

            self.assertEqual(tex_dir, os.path.join(temp_dir, "tex"))

    def test_resolution_is_independent_of_process_cwd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = os.path.join(temp_dir, "elsewhere", "model")
            os.makedirs(model_dir)
            sluggie = os.path.join(model_dir, "model.sluggie")

            with _chdir(temp_dir):
                tex_dir = resolve_tex_dir(sluggie)

            self.assertEqual(tex_dir, os.path.join(model_dir, "tex"))

    def test_resolve_texture_path_joins_tex_dir_and_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = os.path.join(temp_dir, "model")
            os.makedirs(model_dir)
            sluggie = os.path.join(model_dir, "model.sluggie")

            path = resolve_texture_path(sluggie, "0.png")

            self.assertEqual(path, os.path.join(model_dir, "tex", "0.png"))

    def test_resolve_texture_path_accepts_pathlib_sluggie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = os.path.join(temp_dir, "model")
            os.makedirs(model_dir)
            sluggie = pathlib.Path(model_dir) / "model.sluggie"

            path = resolve_texture_path(sluggie, "tex1_64x64_abc_9.png")

            self.assertEqual(path, os.path.join(model_dir, "tex", "tex1_64x64_abc_9.png"))


class ValidateTextureFileNameTests(unittest.TestCase):
    def test_bare_name_is_accepted_unchanged(self):
        self.assertEqual(validate_texture_file_name("0.png"), "0.png")
        self.assertEqual(
            validate_texture_file_name("tex1_64x64_abc_9.png"),
            "tex1_64x64_abc_9.png",
        )

    def test_empty_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            validate_texture_file_name("")

    def test_non_string_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be a string"):
            validate_texture_file_name(0)
        with self.assertRaisesRegex(ValueError, "must be a string"):
            validate_texture_file_name(None)

    def test_posix_directory_component_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "directory component"):
            validate_texture_file_name("sub/0.png")
        with self.assertRaisesRegex(ValueError, "directory component"):
            validate_texture_file_name("../0.png")
        with self.assertRaisesRegex(ValueError, "directory component"):
            validate_texture_file_name("/absolute/0.png")

    def test_windows_directory_component_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "directory component"):
            validate_texture_file_name("sub\\0.png")
        with self.assertRaisesRegex(ValueError, "directory component"):
            validate_texture_file_name("..\\0.png")
        with self.assertRaisesRegex(ValueError, "directory component"):
            validate_texture_file_name("C:\\tex\\0.png")

    def test_dot_segments_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "directory component"):
            validate_texture_file_name(".")
        with self.assertRaisesRegex(ValueError, "directory component"):
            validate_texture_file_name("..")

    def test_reserved_characters_are_rejected(self):
        for name in ("a<b.png", "a>b.png", 'a"b.png', "a|b.png", "a?b.png", "a*b.png"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "reserved character"):
                    validate_texture_file_name(name)

    def test_reserved_device_names_are_rejected(self):
        for name in ("con.png", "NUL.png", "com1.png", "LPT9.png"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "reserved Windows device name"):
                    validate_texture_file_name(name)

    def test_device_name_with_extension_is_still_rejected(self):
        # "con" is the stem of "con.png"; the extension must not mask it.
        with self.assertRaisesRegex(ValueError, "reserved Windows device name"):
            validate_texture_file_name("con.png")


class ValidateTextureDescriptorsTests(unittest.TestCase):
    def test_all_valid_names_return_in_order(self):
        descriptors = [
            {"TextureIndex": 0, "TextureFileName": "0.png"},
            {"TextureIndex": 1, "TextureFileName": "tex1_64x64_abc_9.png"},
        ]
        self.assertEqual(
            validate_texture_descriptors(descriptors),
            ["0.png", "tex1_64x64_abc_9.png"],
        )

    def test_missing_name_is_rejected_with_texture_index(self):
        descriptors = [
            {"TextureIndex": 0, "TextureFileName": "0.png"},
            {"TextureIndex": 1},
        ]
        with self.assertRaisesRegex(ValueError, "texture 1"):
            validate_texture_descriptors(descriptors)

    def test_null_name_is_rejected_with_texture_index(self):
        descriptors = [{"TextureIndex": 3, "TextureFileName": None}]
        with self.assertRaisesRegex(ValueError, "texture 3"):
            validate_texture_descriptors(descriptors)

    def test_unsafe_name_is_rejected_with_texture_index(self):
        descriptors = [
            {"TextureIndex": 0, "TextureFileName": "0.png"},
            {"TextureIndex": 2, "TextureFileName": "..\\0.png"},
        ]
        with self.assertRaisesRegex(ValueError, "texture 2"):
            validate_texture_descriptors(descriptors)

    def test_mixed_valid_and_invalid_list_is_rejected(self):
        descriptors = [
            {"TextureIndex": 0, "TextureFileName": "0.png"},
            {"TextureIndex": 1, "TextureFileName": "ok.png"},
            {"TextureIndex": 2, "TextureFileName": "bad/name.png"},
        ]
        with self.assertRaisesRegex(ValueError, "texture 2"):
            validate_texture_descriptors(descriptors)

    def test_empty_descriptor_list_is_valid(self):
        self.assertEqual(validate_texture_descriptors([]), [])


class PngDimensionTests(unittest.TestCase):
    def _make_png(self, temp_dir: str, width: int, height: int, name: str = "tex.png") -> str:
        from PIL import Image

        path = os.path.join(temp_dir, name)
        Image.new("RGBA", (width, height), (255, 0, 0, 255)).save(path, "PNG")
        return path

    def test_read_png_dimensions_returns_size(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._make_png(temp_dir, 64, 32)
            self.assertEqual(read_png_dimensions(path), (64, 32))

    def test_read_png_dimensions_missing_file_raises_encoding_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = os.path.join(temp_dir, "nope.png")
            with self.assertRaisesRegex(TextureEncodingError, "not found"):
                read_png_dimensions(missing)

    def test_read_png_dimensions_invalid_file_raises_encoding_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bad = os.path.join(temp_dir, "bad.png")
            with open(bad, "wb") as f:
                f.write(b"not a png")
            with self.assertRaisesRegex(TextureEncodingError, "could not read PNG dimensions"):
                read_png_dimensions(bad)

    def test_check_png_dimensions_matching_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._make_png(temp_dir, 128, 64)
            self.assertEqual(check_png_dimensions(path, 128, 64), (128, 64))

    def test_check_png_dimensions_width_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._make_png(temp_dir, 128, 64)
            with self.assertRaisesRegex(ValueError, "128x64 do not match descriptor 256x64"):
                check_png_dimensions(path, 256, 64)

    def test_check_png_dimensions_height_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._make_png(temp_dir, 128, 64)
            with self.assertRaisesRegex(ValueError, "128x64 do not match descriptor 128x128"):
                check_png_dimensions(path, 128, 128)

    def test_encode_rejects_dimension_mismatch_before_wimgt(self):
        # A dimension mismatch must raise ValueError without invoking WIMGT.
        # Use a bogus executable so any WIMGT call would fail differently.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._make_png(temp_dir, 64, 64)
            with self.assertRaisesRegex(ValueError, "do not match descriptor 32x32"):
                encode_png_to_tpl(
                    path,
                    0x6,
                    wimgt_executable="definitely-not-a-real-wimgt",
                    expected_width=32,
                    expected_height=32,
                )

    def test_encode_without_expected_dimensions_skips_check(self):
        # Without expected dimensions the check is skipped and WIMGT is used.
        # With a bogus executable this must surface as a WIMGT error, proving
        # the dimension check was not the cause.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._make_png(temp_dir, 64, 64)
            with self.assertRaises(TextureEncodingError):
                encode_png_to_tpl(
                    path,
                    0x6,
                    wimgt_executable="definitely-not-a-real-wimgt",
                )


def _fake_parsed(width=64, height=64, gx_format=0x6, palette=None):
    """Build a ParsedSingleImageTpl for planner tests without invoking WIMGT."""
    from texture_helper import ParsedSingleImageTpl

    if palette is None:
        palette_offset = 0
        palette_entries = 0
        palette_format = None
        palette_data = b""
        palette_payload_offset = 0
    else:
        palette_offset = 0x100
        palette_entries = palette
        palette_format = 0x2
        palette_data = b"\x00" * (palette * 2)
        palette_payload_offset = 0x200

    return ParsedSingleImageTpl(
        magic=TPL_MAGIC,
        image_count=1,
        table_offset=12,
        image_offset=0x14,
        palette_offset=palette_offset,
        width=width,
        height=height,
        format=gx_format,
        format_name="RGBA8" if gx_format == 0x6 else "C8",
        image_payload_offset=0x40,
        palette_payload_offset=palette_payload_offset,
        image_data=b"\x11" * 64,
        palette_data=palette_data,
        palette_entries=palette_entries,
        palette_format=palette_format,
    )


class BuildTexturePlanTests(unittest.TestCase):
    def _make_model(self, temp_dir: str, names_and_sizes):
        model_dir = os.path.join(temp_dir, "model")
        tex_dir = os.path.join(model_dir, "tex")
        os.makedirs(tex_dir)
        sluggie = os.path.join(model_dir, "model.sluggie")
        with open(sluggie, "w") as f:
            f.write("{}")
        from PIL import Image

        for name, (w, h) in names_and_sizes.items():
            Image.new("RGBA", (w, h), (0, 255, 0, 255)).save(os.path.join(tex_dir, name), "PNG")
        return sluggie

    def test_all_valid_returns_plan_in_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64), "1.png": (32, 32)})
            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 64, "Height": 64, "Format": 0x6},
                {"TextureIndex": 1, "TextureFileName": "1.png", "Width": 32, "Height": 32, "Format": 0x6},
            ]

            def fake_encoder(png_path, gx_format, palette_format=None, **kwargs):
                return _fake_parsed(width=kwargs["expected_width"], height=kwargs["expected_height"], gx_format=gx_format)

            plan = build_texture_plan(sluggie, descriptors, encoder=fake_encoder)

            self.assertIsInstance(plan, TexturePlan)
            self.assertEqual(len(plan), 2)
            self.assertEqual([e.texture_index for e in plan], [0, 1])
            self.assertEqual([e.texture_file_name for e in plan], ["0.png", "1.png"])
            self.assertEqual(plan[0].width, 64)
            self.assertEqual(plan[1].width, 32)

    def test_missing_file_aborts_before_encoder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {})  # no PNGs created
            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 64, "Height": 64, "Format": 0x6},
            ]

            def exploding_encoder(*a, **k):
                raise AssertionError("encoder must not be called for a missing file")

            with self.assertRaises(TextureEncodingError):
                build_texture_plan(sluggie, descriptors, encoder=exploding_encoder)

    def test_bad_dimensions_aborts_before_encoder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64)})
            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 128, "Height": 128, "Format": 0x6},
            ]

            def exploding_encoder(*a, **k):
                raise AssertionError("encoder must not be called for bad dimensions")

            with self.assertRaises(ValueError):
                build_texture_plan(sluggie, descriptors, encoder=exploding_encoder)

    def test_conversion_error_aborts_with_no_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64)})
            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 64, "Height": 64, "Format": 0x6},
            ]

            def failing_encoder(*a, **k):
                raise TextureEncodingError("wimgt exploded")

            with self.assertRaises(TextureEncodingError):
                build_texture_plan(sluggie, descriptors, encoder=failing_encoder)

    def test_metadata_mismatch_aborts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64)})
            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 64, "Height": 64, "Format": 0x6},
            ]

            # Encoder returns a C8 (indexed) TPL for an RGBA8 descriptor.
            def mismatched_encoder(*a, **k):
                return _fake_parsed(width=64, height=64, gx_format=0x9, palette=16)

            with self.assertRaisesRegex(ValueError, "texture 0"):
                build_texture_plan(sluggie, descriptors, encoder=mismatched_encoder)

    def test_mixed_valid_and_invalid_aborts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64), "1.png": (32, 32)})
            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 64, "Height": 64, "Format": 0x6},
                {"TextureIndex": 1, "TextureFileName": "1.png", "Width": 32, "Height": 32, "Format": 0x6},
            ]

            calls = []

            def selective_encoder(png_path, gx_format, palette_format=None, **kwargs):
                calls.append(png_path)
                if "1.png" in png_path:
                    raise TextureEncodingError("second texture failed")
                return _fake_parsed(width=kwargs["expected_width"], height=kwargs["expected_height"], gx_format=gx_format)

            with self.assertRaises(TextureEncodingError):
                build_texture_plan(sluggie, descriptors, encoder=selective_encoder)
            # Both textures were attempted, but no plan was returned because the
            # second failed — the all-or-nothing gate held.
            self.assertEqual(len(calls), 2)

    def test_empty_descriptors_returns_empty_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = os.path.join(temp_dir, "model")
            os.makedirs(os.path.join(model_dir, "tex"))
            sluggie = os.path.join(model_dir, "model.sluggie")
            with open(sluggie, "w") as f:
                f.write("{}")

            plan = build_texture_plan(sluggie, [], encoder=lambda *a, **k: _fake_parsed())

            self.assertIsInstance(plan, TexturePlan)
            self.assertEqual(len(plan), 0)

    def test_encoder_receives_expected_dimensions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64)})
            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 64, "Height": 64, "Format": 0x6},
            ]
            seen = {}

            def capturing_encoder(png_path, gx_format, palette_format=None, **kwargs):
                seen["width"] = kwargs.get("expected_width")
                seen["height"] = kwargs.get("expected_height")
                return _fake_parsed(width=64, height=64, gx_format=gx_format)

            build_texture_plan(sluggie, descriptors, encoder=capturing_encoder)

            self.assertEqual(seen["width"], 64)
            self.assertEqual(seen["height"], 64)

    def test_extra_files_in_tex_dir_are_ignored(self):
        # Unrelated files in tex/ must not affect the plan: only the
        # descriptor's TextureFileName is resolved (PLAN 3.1 gate).
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64)})
            tex_dir = os.path.join(os.path.dirname(sluggie), "tex")
            from PIL import Image

            # Extra, unrelated files that must be ignored.
            Image.new("RGBA", (16, 16), (9, 9, 9, 255)).save(os.path.join(tex_dir, "unrelated.png"), "PNG")
            with open(os.path.join(tex_dir, "notes.txt"), "w") as f:
                f.write("not a texture")
            os.makedirs(os.path.join(tex_dir, "subdir"), exist_ok=True)

            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 64, "Height": 64, "Format": 0x6},
            ]

            def fake_encoder(png_path, gx_format, palette_format=None, **kwargs):
                self.assertNotIn("unrelated", png_path)
                return _fake_parsed(width=kwargs["expected_width"], height=kwargs["expected_height"], gx_format=gx_format)

            plan = build_texture_plan(sluggie, descriptors, encoder=fake_encoder)

            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0].texture_file_name, "0.png")
            self.assertEqual(plan.skipped, ())


class MipSkipTests(unittest.TestCase):
    """PLAN 3.1, fifth bullet: a validated unsupported mip layout is a
    skipped texture with a warning; all other failures remain fatal."""

    def _make_model(self, temp_dir: str, names_and_sizes):
        model_dir = os.path.join(temp_dir, "model")
        tex_dir = os.path.join(model_dir, "tex")
        os.makedirs(tex_dir)
        sluggie = os.path.join(model_dir, "model.sluggie")
        with open(sluggie, "w") as f:
            f.write("{}")
        from PIL import Image

        for name, (w, h) in names_and_sizes.items():
            Image.new("RGBA", (w, h), (0, 255, 0, 255)).save(os.path.join(tex_dir, name), "PNG")
        return sluggie

    def test_mipmapped_descriptor_is_skipped_with_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (256, 32)})
            descriptors = [
                {
                    "TextureIndex": 46,
                    "TextureFileName": "0.png",
                    "Width": 256,
                    "Height": 32,
                    "Format": 0xE,
                    "AdditionalMipCount": 3,
                    "ImagePayloadLength": 5440,
                },
            ]

            def exploding_encoder(*a, **k):
                raise AssertionError("encoder must not be called for a skipped mip texture")

            warnings = []
            plan = build_texture_plan(
                sluggie,
                descriptors,
                encoder=exploding_encoder,
                warn=warnings.append,
            )

            # The plan is returned (not fatal) with the texture skipped.
            self.assertEqual(len(plan), 0)
            self.assertEqual(len(plan.skipped), 1)
            skipped = plan.skipped[0]
            self.assertIsInstance(skipped, SkippedTexture)
            self.assertEqual(skipped.texture_index, 46)
            self.assertEqual(skipped.texture_file_name, "0.png")
            self.assertEqual(skipped.expected_payload_length, 5440)
            self.assertIn("unsupported mip layout", skipped.reason)

            # One warning was logged, naming the texture and the skip.
            self.assertEqual(len(warnings), 1)
            self.assertIn("texture 46", warnings[0])
            self.assertIn("0.png", warnings[0])
            self.assertIn("left unchanged", warnings[0])

    def test_mip_skip_falls_back_to_calculated_payload_length(self):
        # Without ImagePayloadLength the expected length is derived from the
        # proven donor contract (256x32 CMPR, 3 additional mips).
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (256, 32)})
            descriptors = [
                {
                    "TextureIndex": 0,
                    "TextureFileName": "0.png",
                    "Width": 256,
                    "Height": 32,
                    "Format": 0xE,
                    "AdditionalMipCount": 3,
                },
            ]

            plan = build_texture_plan(
                sluggie, descriptors,
                encoder=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not encode")),
                warn=lambda m: None,
            )

            # 256x32 + 128x16 + 64x8 + 32x4 CMPR levels (4x4 blocks / 8 bytes):
            # 4096 + 1024 + 256 + 64 = 5440 bytes (matches the proven donor).
            self.assertEqual(plan.skipped[0].expected_payload_length, 5440)

    def test_mip_skip_continues_with_other_valid_textures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64), "1.png": (256, 32)})
            descriptors = [
                {"TextureIndex": 0, "TextureFileName": "0.png", "Width": 64, "Height": 64, "Format": 0x6},
                {
                    "TextureIndex": 1,
                    "TextureFileName": "1.png",
                    "Width": 256,
                    "Height": 32,
                    "Format": 0xE,
                    "AdditionalMipCount": 3,
                    "ImagePayloadLength": 5440,
                },
            ]

            def fake_encoder(png_path, gx_format, palette_format=None, **kwargs):
                return _fake_parsed(width=kwargs["expected_width"], height=kwargs["expected_height"], gx_format=gx_format)

            warnings = []
            plan = build_texture_plan(
                sluggie, descriptors, encoder=fake_encoder, warn=warnings.append,
            )

            # The valid texture is planned; the mip texture is skipped.
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0].texture_index, 0)
            self.assertEqual(len(plan.skipped), 1)
            self.assertEqual(plan.skipped[0].texture_index, 1)
            self.assertEqual(len(warnings), 1)

    def test_mip_skip_failure_still_aborts_on_other_errors(self):
        # The skip is nonfatal, but a *different* fatal failure (missing file)
        # must still abort with no plan, even when a mip texture is skipped.
        with tempfile.TemporaryDirectory() as temp_dir:
            # Only the mip texture's PNG exists; the base texture's is missing.
            sluggie = self._make_model(temp_dir, {"1.png": (256, 32)})
            descriptors = [
                {
                    "TextureIndex": 0,
                    "TextureFileName": "missing.png",
                    "Width": 64,
                    "Height": 64,
                    "Format": 0x6,
                },
                {
                    "TextureIndex": 1,
                    "TextureFileName": "1.png",
                    "Width": 256,
                    "Height": 32,
                    "Format": 0xE,
                    "AdditionalMipCount": 2,
                },
            ]

            with self.assertRaises(TextureEncodingError):
                build_texture_plan(sluggie, descriptors, warn=lambda m: None)

    def test_mip_texture_missing_file_still_fatal(self):
        # The skip exception does not cover missing files: a mipmapped
        # descriptor whose PNG is absent must abort, not skip.
        with tempfile.TemporaryDirectory() as temp_dir:
            # No PNGs created at all.
            model_dir = os.path.join(temp_dir, "model")
            os.makedirs(os.path.join(model_dir, "tex"))
            sluggie = os.path.join(model_dir, "model.sluggie")
            with open(sluggie, "w") as f:
                f.write("{}")
            descriptors = [
                {
                    "TextureIndex": 0,
                    "TextureFileName": "missing.png",
                    "Width": 256,
                    "Height": 32,
                    "Format": 0xE,
                    "AdditionalMipCount": 3,
                },
            ]

            with self.assertRaises(TextureEncodingError):
                build_texture_plan(sluggie, descriptors, warn=lambda m: None)

    def test_mip_texture_bad_dimensions_still_fatal(self):
        # The skip exception does not cover bad dimensions: a mipmapped
        # descriptor whose PNG size differs must abort, not skip.
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64)})
            descriptors = [
                {
                    "TextureIndex": 0,
                    "TextureFileName": "0.png",
                    "Width": 256,
                    "Height": 32,
                    "Format": 0xE,
                    "AdditionalMipCount": 3,
                },
            ]

            with self.assertRaises(ValueError):
                build_texture_plan(sluggie, descriptors, warn=lambda m: None)

    def test_zero_mip_count_is_not_skipped(self):
        # AdditionalMipCount of 0 (or absent) is a base-only texture and must
        # go through the normal encode path, not the skip path.
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = self._make_model(temp_dir, {"0.png": (64, 64)})
            descriptors = [
                {
                    "TextureIndex": 0,
                    "TextureFileName": "0.png",
                    "Width": 64,
                    "Height": 64,
                    "Format": 0x6,
                    "AdditionalMipCount": 0,
                },
            ]

            def fake_encoder(png_path, gx_format, palette_format=None, **kwargs):
                return _fake_parsed(width=kwargs["expected_width"], height=kwargs["expected_height"], gx_format=gx_format)

            plan = build_texture_plan(sluggie, descriptors, encoder=fake_encoder, warn=lambda m: None)

            self.assertEqual(len(plan), 1)
            self.assertEqual(plan.skipped, ())


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
