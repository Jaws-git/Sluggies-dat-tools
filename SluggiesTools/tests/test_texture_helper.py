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
    build_unpatch_texture_writes,
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


class TextureWriteRepresentationTests(unittest.TestCase):
    """PLAN 3.2, first bullet: writes are represented as
    ``(kind, texture_index, offset, payload_length, bytes)``."""

    def test_write_unpacks_as_the_five_field_tuple(self):
        write = TextureWrite(
            kind="image",
            texture_index=7,
            offset=0x1000,
            payload_length=4,
            bytes=b"\x01\x02\x03\x04",
        )

        self.assertEqual(
            tuple(write),
            ("image", 7, 0x1000, 4, b"\x01\x02\x03\x04"),
        )
        # Field access matches the tuple positions.
        self.assertEqual(write.kind, "image")
        self.assertEqual(write.texture_index, 7)
        self.assertEqual(write.offset, 0x1000)
        self.assertEqual(write.payload_length, 4)
        self.assertEqual(write.bytes, b"\x01\x02\x03\x04")

    def test_write_is_frozen(self):
        write = TextureWrite("image", 0, 0, 1, b"\x00")
        with self.assertRaises(AttributeError):
            write.offset = 1  # type: ignore[misc]

    def test_write_rejects_bytes_length_mismatch(self):
        with self.assertRaisesRegex(ValueError, "malformed"):
            TextureWrite("image", 0, 0, 4, b"\x00\x00")
        with self.assertRaisesRegex(ValueError, "malformed"):
            TextureWrite("palette", 3, 0x10, 2, b"\x00\x00\x00")

    def test_write_rejects_negative_offset_and_length(self):
        with self.assertRaisesRegex(ValueError, "negative offset"):
            TextureWrite("image", 0, -1, 1, b"\x00")
        with self.assertRaisesRegex(ValueError, "negative payload_length"):
            TextureWrite("image", 0, 0, -1, b"")


def _entry(image: bytes, palette: bytes = b"") -> TexturePlanEntry:
    return TexturePlanEntry(
        texture_index=0,
        texture_file_name="0.png",
        width=4,
        height=4,
        format=0x6,
        format_name="RGBA8",
        image_data=image,
        palette_data=palette,
        palette_entries=len(palette) // 2 if palette else 0,
        palette_format=0x2 if palette else None,
    )


class TextureWritesForTests(unittest.TestCase):
    """PLAN 3.2: one descriptor/entry pair yields an image write plus an
    optional palette write, each carrying the proven payload length."""

    def test_image_only_descriptor_yields_one_image_write(self):
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": 64,
        }
        entry = _entry(b"\x11" * 64)

        writes = texture_writes_for(descriptor, entry)

        self.assertEqual(len(writes), 1)
        write = writes[0]
        self.assertEqual(write.kind, "image")
        self.assertEqual(write.texture_index, 0)
        self.assertEqual(write.offset, 0x1000)
        self.assertEqual(write.payload_length, 64)
        self.assertEqual(write.bytes, b"\x11" * 64)

    def test_indexed_descriptor_yields_image_and_palette_writes(self):
        descriptor = {
            "TextureIndex": 2,
            "ImageDataOffset": "0x2000",
            "ImagePayloadLength": 32,
            "PaletteDataOffset": "0x3000",
            "PaletteDataLength": 8,
        }
        entry = _entry(b"\x22" * 32, palette=b"\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11")

        writes = texture_writes_for(descriptor, entry)

        self.assertEqual(len(writes), 2)
        image, palette = writes
        self.assertEqual(image.kind, "image")
        self.assertEqual(image.offset, 0x2000)
        self.assertEqual(image.payload_length, 32)
        self.assertEqual(palette.kind, "palette")
        self.assertEqual(palette.texture_index, 2)
        self.assertEqual(palette.offset, 0x3000)
        self.assertEqual(palette.payload_length, 8)
        self.assertEqual(palette.bytes, b"\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11")

    def test_payload_length_comes_from_the_descriptor(self):
        # The proven length is the descriptor's, not the encoded length.
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": 64,
        }
        entry = _entry(b"\x11" * 64)

        write = texture_writes_for(descriptor, entry)[0]
        self.assertEqual(write.payload_length, 64)

    def test_missing_image_offset_is_rejected(self):
        descriptor = {"TextureIndex": 0, "ImagePayloadLength": 64}
        with self.assertRaisesRegex(ValueError, "missing ImageDataOffset"):
            texture_writes_for(descriptor, _entry(b"\x11" * 64))

    def test_invalid_image_offset_is_rejected(self):
        descriptor = {"TextureIndex": 0, "ImageDataOffset": "not-a-hex", "ImagePayloadLength": 64}
        with self.assertRaisesRegex(ValueError, "invalid ImageDataOffset"):
            texture_writes_for(descriptor, _entry(b"\x11" * 64))

    def test_missing_palette_offset_is_rejected_when_palette_present(self):
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": 32,
            # PaletteDataOffset intentionally absent.
            "PaletteDataLength": 8,
        }
        entry = _entry(b"\x22" * 32, palette=b"\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11")
        with self.assertRaisesRegex(ValueError, "missing PaletteDataOffset"):
            texture_writes_for(descriptor, entry)

    def test_palette_length_mismatch_is_rejected(self):
        # The proven palette length (8) does not match the encoded bytes (4),
        # so the write invariant must reject it.
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": 32,
            "PaletteDataOffset": "0x3000",
            "PaletteDataLength": 8,
        }
        entry = _entry(b"\x22" * 32, palette=b"\xAA\xBB\xCC\xDD")
        with self.assertRaisesRegex(ValueError, "malformed"):
            texture_writes_for(descriptor, entry)

    def test_missing_image_payload_length_is_rejected(self):
        # PLAN 3.2, second bullet: the proven image payload length is required.
        # A descriptor without ImagePayloadLength cannot be validated against
        # the donor, so the write must be rejected (no fallback to the encoded
        # length).
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            # ImagePayloadLength intentionally absent.
        }
        with self.assertRaisesRegex(ValueError, "missing the proven image payload length"):
            texture_writes_for(descriptor, _entry(b"\x11" * 64))

    def test_missing_palette_payload_length_is_rejected(self):
        # The proven palette length is required when a palette is present.
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": 32,
            "PaletteDataOffset": "0x3000",
            # PaletteDataLength intentionally absent.
        }
        entry = _entry(b"\x22" * 32, palette=b"\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11")
        with self.assertRaisesRegex(ValueError, "missing the proven palette payload length"):
            texture_writes_for(descriptor, entry)

    def test_invalid_image_payload_length_is_rejected(self):
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": "not-a-number",
        }
        with self.assertRaisesRegex(ValueError, "invalid ImagePayloadLength"):
            texture_writes_for(descriptor, _entry(b"\x11" * 64))

    def test_negative_image_payload_length_is_rejected(self):
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": -1,
        }
        with self.assertRaisesRegex(ValueError, "negative ImagePayloadLength"):
            texture_writes_for(descriptor, _entry(b"\x11" * 64))

    def test_payload_length_exceeding_capacity_is_rejected(self):
        # A proven payload length that exceeds the buffer capacity would write
        # beyond the buffer, so it must be rejected.
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": 128,
            "ImageDataCapacity": 64,
        }
        with self.assertRaisesRegex(ValueError, "exceeds the buffer capacity"):
            texture_writes_for(descriptor, _entry(b"\x11" * 128))

    def test_write_covers_only_proven_length_preserving_capacity_tail(self):
        # PLAN 3.2, second bullet: the write covers exactly the proven payload
        # length, preserving any remaining bytes between ImagePayloadLength and
        # ImageDataCapacity. The encoded bytes must be exactly the proven
        # length; the capacity tail is left untouched in the DAT.
        descriptor = {
            "TextureIndex": 0,
            "ImageDataOffset": "0x1000",
            "ImagePayloadLength": 64,
            "ImageDataCapacity": 128,  # 64 bytes of capacity tail to preserve.
        }
        entry = _entry(b"\x11" * 64)

        write = texture_writes_for(descriptor, entry)[0]

        self.assertEqual(write.payload_length, 64)
        self.assertEqual(len(write.bytes), 64)
        # The write ends at 0x1000 + 64, leaving the 64-byte capacity tail
        # (0x1040..0x1080) untouched.
        self.assertEqual(write.offset + write.payload_length, 0x1000 + 64)


class BuildTextureWritesTests(unittest.TestCase):
    """PLAN 3.2: the complete write list is built from a plan without opening
    the output DAT for writing, and every write is bounds-checked against both
    the input and output DAT sizes (third bullet)."""

    def test_writes_are_built_in_plan_order(self):
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
            {"TextureIndex": 1, "ImageDataOffset": "0x2000", "ImagePayloadLength": 32},
        ]
        plan = TexturePlan(
            entries=(
                _entry(b"\x11" * 64),
                TexturePlanEntry(
                    texture_index=1,
                    texture_file_name="1.png",
                    width=4,
                    height=4,
                    format=0x6,
                    format_name="RGBA8",
                    image_data=b"\x22" * 32,
                    palette_data=b"",
                    palette_entries=0,
                    palette_format=None,
                ),
            ),
        )

        writes = build_texture_writes(descriptors, plan, 0x10000, 0x10000)

        self.assertEqual(len(writes), 2)
        self.assertEqual([w.texture_index for w in writes], [0, 1])
        self.assertEqual([w.offset for w in writes], [0x1000, 0x2000])
        self.assertEqual([w.payload_length for w in writes], [64, 32])

    def test_indexed_plan_yields_image_and_palette_writes(self):
        descriptors = [
            {
                "TextureIndex": 0,
                "ImageDataOffset": "0x1000",
                "ImagePayloadLength": 32,
                "PaletteDataOffset": "0x3000",
                "PaletteDataLength": 8,
            },
        ]
        plan = TexturePlan(entries=(_entry(b"\x22" * 32, palette=b"\xAA" * 8),))

        writes = build_texture_writes(descriptors, plan, 0x10000, 0x10000)

        self.assertEqual(len(writes), 2)
        self.assertEqual([w.kind for w in writes], ["image", "palette"])

    def test_entry_without_matching_descriptor_is_rejected(self):
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(
            entries=(
                TexturePlanEntry(
                    texture_index=99,
                    texture_file_name="99.png",
                    width=4,
                    height=4,
                    format=0x6,
                    format_name="RGBA8",
                    image_data=b"\x11" * 64,
                    palette_data=b"",
                    palette_entries=0,
                    palette_format=None,
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "no matching descriptor"):
            build_texture_writes(descriptors, plan, 0x10000, 0x10000)

    def test_empty_plan_yields_no_writes(self):
        self.assertEqual(build_texture_writes([], TexturePlan(entries=()), 0x10000, 0x10000), ())

    # --- PLAN 3.2, third bullet: bounds-check against both DAT sizes ---

    def test_write_exceeding_input_dat_size_is_rejected(self):
        # The write ends at 0x1000 + 64 = 0x1040, which is past the input DAT
        # size of 0x1000, so it must be rejected.
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(entries=(_entry(b"\x11" * 64),))
        with self.assertRaisesRegex(ValueError, "past the input DAT size"):
            build_texture_writes(descriptors, plan, 0x1000, 0x10000)

    def test_write_exceeding_output_dat_size_is_rejected(self):
        # The write ends at 0x1000 + 64 = 0x1040, which is past the output DAT
        # size of 0x1000, so it must be rejected even though the input is large.
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(entries=(_entry(b"\x11" * 64),))
        with self.assertRaisesRegex(ValueError, "past the output DAT size"):
            build_texture_writes(descriptors, plan, 0x10000, 0x1000)

    def test_write_exactly_at_dat_boundary_is_allowed(self):
        # The write ends at 0x1000 + 64 = 0x1040, exactly the DAT size, so it
        # is in-bounds (end == size is allowed; only end > size is rejected).
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(entries=(_entry(b"\x11" * 64),))
        writes = build_texture_writes(descriptors, plan, 0x1040, 0x1040)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].offset + writes[0].payload_length, 0x1040)

    def test_palette_write_exceeding_dat_size_is_rejected(self):
        # The palette write ends at 0x3000 + 8 = 0x3008, past the DAT size of
        # 0x3000, so it must be rejected.
        descriptors = [
            {
                "TextureIndex": 0,
                "ImageDataOffset": "0x1000",
                "ImagePayloadLength": 32,
                "PaletteDataOffset": "0x3000",
                "PaletteDataLength": 8,
            },
        ]
        plan = TexturePlan(entries=(_entry(b"\x22" * 32, palette=b"\xAA" * 8),))
        with self.assertRaisesRegex(ValueError, "past the input DAT size"):
            build_texture_writes(descriptors, plan, 0x3000, 0x10000)

    def test_missing_input_dat_size_is_rejected(self):
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(entries=(_entry(b"\x11" * 64),))
        with self.assertRaisesRegex(ValueError, "missing input DAT size"):
            build_texture_writes(descriptors, plan, None, 0x10000)

    def test_missing_output_dat_size_is_rejected(self):
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(entries=(_entry(b"\x11" * 64),))
        with self.assertRaisesRegex(ValueError, "missing output DAT size"):
            build_texture_writes(descriptors, plan, 0x10000, None)

    def test_negative_dat_size_is_rejected(self):
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(entries=(_entry(b"\x11" * 64),))
        with self.assertRaisesRegex(ValueError, "negative input DAT size"):
            build_texture_writes(descriptors, plan, -1, 0x10000)

    # --- PLAN 3.2, fourth bullet: overlap detection and alias coalescing ---

    def test_identical_alias_writes_are_coalesced(self):
        # Two descriptors sharing the same image offset with identical layout
        # and bytes should be coalesced into one write.
        image_bytes = b"\x11" * 64
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
            {"TextureIndex": 1, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(
            entries=(
                _entry(image_bytes),
                TexturePlanEntry(
                    texture_index=1,
                    texture_file_name="1.png",
                    width=4,
                    height=4,
                    format=0x6,
                    format_name="RGBA8",
                    image_data=image_bytes,
                    palette_data=b"",
                    palette_entries=0,
                    palette_format=None,
                ),
            ),
        )
        writes = build_texture_writes(descriptors, plan, 0x10000, 0x10000)
        # Only one write should remain after coalescing.
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].offset, 0x1000)
        self.assertEqual(writes[0].bytes, image_bytes)

    def test_different_layout_alias_is_rejected(self):
        # Two descriptors at the same offset but different payload lengths
        # are a different-layout alias and must be rejected.
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
            {"TextureIndex": 1, "ImageDataOffset": "0x1000", "ImagePayloadLength": 32},
        ]
        plan = TexturePlan(
            entries=(
                _entry(b"\x11" * 64),
                TexturePlanEntry(
                    texture_index=1,
                    texture_file_name="1.png",
                    width=4,
                    height=4,
                    format=0x6,
                    format_name="RGBA8",
                    image_data=b"\x22" * 32,
                    palette_data=b"",
                    palette_entries=0,
                    palette_format=None,
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "different-layout alias"):
            build_texture_writes(descriptors, plan, 0x10000, 0x10000)

    def test_conflicting_same_offset_same_length_different_bytes_is_rejected(self):
        # Two descriptors at the same offset with the same payload length
        # but different bytes are a conflicting overlap.
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
            {"TextureIndex": 1, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(
            entries=(
                _entry(b"\x11" * 64),
                TexturePlanEntry(
                    texture_index=1,
                    texture_file_name="1.png",
                    width=4,
                    height=4,
                    format=0x6,
                    format_name="RGBA8",
                    image_data=b"\x22" * 64,
                    palette_data=b"",
                    palette_entries=0,
                    palette_format=None,
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "conflicting overlap"):
            build_texture_writes(descriptors, plan, 0x10000, 0x10000)

    def test_partially_overlapping_writes_are_rejected(self):
        # Two writes at different offsets whose ranges overlap must be rejected.
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
            {"TextureIndex": 1, "ImageDataOffset": "0x1020", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(
            entries=(
                _entry(b"\x11" * 64),
                TexturePlanEntry(
                    texture_index=1,
                    texture_file_name="1.png",
                    width=4,
                    height=4,
                    format=0x6,
                    format_name="RGBA8",
                    image_data=b"\x22" * 64,
                    palette_data=b"",
                    palette_entries=0,
                    palette_format=None,
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "overlaps with"):
            build_texture_writes(descriptors, plan, 0x10000, 0x10000)

    def test_non_overlapping_writes_are_preserved(self):
        # Two writes at different offsets that don't overlap should both pass.
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
            {"TextureIndex": 1, "ImageDataOffset": "0x2000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(
            entries=(
                _entry(b"\x11" * 64),
                TexturePlanEntry(
                    texture_index=1,
                    texture_file_name="1.png",
                    width=4,
                    height=4,
                    format=0x6,
                    format_name="RGBA8",
                    image_data=b"\x22" * 64,
                    palette_data=b"",
                    palette_entries=0,
                    palette_format=None,
                ),
            ),
        )
        writes = build_texture_writes(descriptors, plan, 0x10000, 0x10000)
        self.assertEqual(len(writes), 2)
        # Writes are sorted by offset.
        self.assertEqual(writes[0].offset, 0x1000)
        self.assertEqual(writes[1].offset, 0x2000)

    def test_writes_are_sorted_by_offset(self):
        # Plan entries in reverse offset order should produce writes sorted by offset.
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x2000", "ImagePayloadLength": 64},
            {"TextureIndex": 1, "ImageDataOffset": "0x1000", "ImagePayloadLength": 64},
        ]
        plan = TexturePlan(
            entries=(
                _entry(b"\x11" * 64),
                TexturePlanEntry(
                    texture_index=1,
                    texture_file_name="1.png",
                    width=4,
                    height=4,
                    format=0x6,
                    format_name="RGBA8",
                    image_data=b"\x22" * 64,
                    palette_data=b"",
                    palette_entries=0,
                    palette_format=None,
                ),
            ),
        )
        writes = build_texture_writes(descriptors, plan, 0x10000, 0x10000)
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0].offset, 0x1000)
        self.assertEqual(writes[1].offset, 0x2000)


class BuildUnpatchTextureWritesTests(unittest.TestCase):
    """PLAN 4.1: unpatch mode reads original image and palette bytes from the
    input DAT at the descriptor's proven offsets and lengths, without touching
    PNG files or invoking WIMGT."""

    def _make_dat(self, size: int, fill: bytes = b"\xAB") -> bytes:
        return (fill * size)[:size]

    def _write_dat(self, path: str, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)

    def test_image_only_descriptor_restores_image_bytes(self):
        image_bytes = b"\xDE\xAD\xBE\xEF" * 16  # 64 bytes
        dat = b"\x00" * 0x1000 + image_bytes + b"\x00" * 0x1000
        descriptors = [
            {
                "TextureIndex": 0,
                "ImageDataOffset": "0x1000",
                "ImagePayloadLength": 64,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dat_path = os.path.join(tmp, "dt_na.dat")
            self._write_dat(dat_path, dat)
            writes = build_unpatch_texture_writes(
                descriptors, dat_path, len(dat), len(dat)
            )
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].kind, "image")
        self.assertEqual(writes[0].texture_index, 0)
        self.assertEqual(writes[0].offset, 0x1000)
        self.assertEqual(writes[0].payload_length, 64)
        self.assertEqual(writes[0].bytes, image_bytes)

    def test_paletted_descriptor_restores_image_and_palette(self):
        image_bytes = b"\x11" * 32
        palette_bytes = b"\x22" * 16
        dat = b"\x00" * 0x1000 + image_bytes + b"\x00" * 0x100 + palette_bytes + b"\x00" * 0x100
        descriptors = [
            {
                "TextureIndex": 2,
                "ImageDataOffset": "0x1000",
                "ImagePayloadLength": 32,
                "PaletteDataOffset": hex(0x1000 + 32 + 0x100),
                "PaletteDataLength": 16,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dat_path = os.path.join(tmp, "dt_na.dat")
            self._write_dat(dat_path, dat)
            writes = build_unpatch_texture_writes(
                descriptors, dat_path, len(dat), len(dat)
            )
        self.assertEqual(len(writes), 2)
        kinds = [w.kind for w in writes]
        self.assertIn("image", kinds)
        self.assertIn("palette", kinds)
        by_kind = {w.kind: w for w in writes}
        self.assertEqual(by_kind["image"].bytes, image_bytes)
        self.assertEqual(by_kind["palette"].bytes, palette_bytes)

    def test_missing_image_payload_length_is_rejected(self):
        dat = b"\x00" * 0x100
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x10"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dat_path = os.path.join(tmp, "dt_na.dat")
            self._write_dat(dat_path, dat)
            with self.assertRaisesRegex(ValueError, "missing the proven image payload length"):
                build_unpatch_texture_writes(
                    descriptors, dat_path, len(dat), len(dat)
                )

    def test_write_out_of_bounds_is_rejected(self):
        dat = b"\x00" * 0x100
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0xF0", "ImagePayloadLength": 64},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dat_path = os.path.join(tmp, "dt_na.dat")
            self._write_dat(dat_path, dat)
            with self.assertRaisesRegex(ValueError, "past the input DAT size"):
                build_unpatch_texture_writes(
                    descriptors, dat_path, len(dat), len(dat)
                )

    def test_empty_descriptor_list_yields_no_writes(self):
        dat = b"\x00" * 0x100
        with tempfile.TemporaryDirectory() as tmp:
            dat_path = os.path.join(tmp, "dt_na.dat")
            self._write_dat(dat_path, dat)
            writes = build_unpatch_texture_writes([], dat_path, len(dat), len(dat))
        self.assertEqual(writes, ())

    def test_identical_alias_is_coalesced(self):
        image_bytes = b"\xCC" * 32
        dat = b"\x00" * 0x100 + image_bytes + b"\x00" * 0x100
        descriptors = [
            {"TextureIndex": 0, "ImageDataOffset": "0x100", "ImagePayloadLength": 32},
            {"TextureIndex": 1, "ImageDataOffset": "0x100", "ImagePayloadLength": 32},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dat_path = os.path.join(tmp, "dt_na.dat")
            self._write_dat(dat_path, dat)
            writes = build_unpatch_texture_writes(
                descriptors, dat_path, len(dat), len(dat)
            )
        # Two descriptors sharing the same pointer produce one write.
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].bytes, image_bytes)


class PatchAllOrNothingGateTests(unittest.TestCase):
    """PLAN 4.1 gate: when any texture fails to encode, build_texture_plan()
    raises before returning a plan. patch_inplace.py catches this exception and
    calls abort() before the write phase, so no geometry or texture bytes are
    committed to the output DAT."""

    def test_second_encoder_failure_raises_and_discards_first_result(self):
        """build_texture_plan raises when the second encoder call fails.

        Both encoders are attempted (all-or-nothing after validation), the
        second raises, and no plan is returned. In patch_inplace.py this causes
        abort() to run before any DAT writes, so the queued geometry patches
        are also left unwritten.
        """
        calls = []

        def mock_encoder(path, gx_format, palette_format=None, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                return _fake_parsed()
            raise TextureEncodingError("second texture encoding failed deliberately")

        descriptors = [
            {"TextureIndex": 0, "TextureFileName": "0.png"},
            {"TextureIndex": 1, "TextureFileName": "1.png"},
        ]

        with self.assertRaises(TextureEncodingError) as ctx:
            build_texture_plan("/fake/model/model.sluggie", descriptors, encoder=mock_encoder)

        self.assertIn("second texture encoding failed", str(ctx.exception))
        # Both descriptors were attempted; the second caused the failure.
        self.assertEqual(len(calls), 2)

    def test_first_encoder_failure_also_prevents_plan(self):
        """build_texture_plan raises even when only the first encoder fails."""
        def failing_encoder(path, gx_format, palette_format=None, **kwargs):
            raise TextureEncodingError("first texture failed")

        descriptors = [
            {"TextureIndex": 0, "TextureFileName": "0.png"},
        ]

        with self.assertRaises(TextureEncodingError):
            build_texture_plan("/fake/model/model.sluggie", descriptors, encoder=failing_encoder)

    def test_value_error_from_encoder_also_prevents_plan(self):
        """ValueError from the encoder (e.g. dimension mismatch) is also fatal."""
        def bad_encoder(path, gx_format, palette_format=None, **kwargs):
            raise ValueError("encoded dimensions do not match descriptor")

        descriptors = [
            {"TextureIndex": 0, "TextureFileName": "0.png"},
        ]

        with self.assertRaises(ValueError):
            build_texture_plan("/fake/model/model.sluggie", descriptors, encoder=bad_encoder)


class VerifyWriteRoundTripTests(unittest.TestCase):
    """PLAN 4.2 gate: apply texture writes to a temp DAT, verify byte-for-byte,
    then unpatch and confirm that the restored ranges match the input DAT exactly."""

    def _write_file(self, path: str, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)

    def _read_file(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def test_unpatch_round_trip_restores_input_ranges(self):
        """Unpatch writes applied to a modified output DAT restore the input ranges.

        This is the primary 4.2 gate: start with a known input DAT, produce an
        output DAT with different bytes at the image and palette ranges, use
        build_unpatch_texture_writes to build restoration writes, apply them with
        byte-for-byte verification, then confirm the ranges match the input DAT.
        """
        image_bytes = b"\xAA" * 64
        palette_bytes = b"\xBB" * 32
        img_off = 0x100
        pal_off = 0x180
        dat_size = 0x200

        # Input DAT with original image and palette bytes.
        input_dat = bytearray(dat_size)
        input_dat[img_off : img_off + len(image_bytes)] = image_bytes
        input_dat[pal_off : pal_off + len(palette_bytes)] = palette_bytes
        input_dat = bytes(input_dat)

        # Output DAT with different bytes at those ranges (simulating a prior patch).
        output_dat = bytearray(dat_size)
        output_dat[img_off : img_off + len(image_bytes)] = b"\xFF" * len(image_bytes)
        output_dat[pal_off : pal_off + len(palette_bytes)] = b"\xEE" * len(palette_bytes)

        descriptors = [{
            "TextureIndex": 0,
            "ImageDataOffset": f"0x{img_off:X}",
            "ImagePayloadLength": len(image_bytes),
            "ImageDataCapacity": len(image_bytes),
            "PaletteDataOffset": f"0x{pal_off:X}",
            "PaletteDataLength": len(palette_bytes),
        }]

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.dat")
            output_path = os.path.join(tmp, "output.dat")
            self._write_file(input_path, input_dat)
            self._write_file(output_path, bytes(output_dat))

            writes = build_unpatch_texture_writes(
                descriptors, input_path, dat_size, dat_size,
            )

            # Apply and verify each write (mirrors the patch_inplace.py 4.2 loop).
            with open(output_path, "r+b") as f:
                for write in writes:
                    f.seek(write.offset)
                    f.write(write.bytes)
                    f.seek(write.offset)
                    readback = f.read(write.payload_length)
                    self.assertEqual(
                        readback, write.bytes,
                        msg=f"write verification failed for texture {write.texture_index} {write.kind}",
                    )

            result = self._read_file(output_path)

        # The image and palette ranges in the output now match the input exactly.
        self.assertEqual(result[img_off : img_off + len(image_bytes)], image_bytes)
        self.assertEqual(result[pal_off : pal_off + len(palette_bytes)], palette_bytes)

    def test_patch_writes_known_bytes_and_are_verified(self):
        """build_texture_writes with a fake encoder produces writes that apply and
        verify correctly (patch side of the round-trip gate)."""
        known_image = b"\x11" * 64
        img_off = 0x100
        dat_size = 0x200

        def fake_encoder(path, gx_format, palette_format=None, **kwargs):
            return _fake_parsed(width=64, height=64, gx_format=0x6)

        # Width/Height omitted intentionally: build_texture_plan skips the PIL
        # dimension check when they are absent, letting the test run without PIL.
        descriptors = [{
            "TextureIndex": 7,
            "TextureFileName": "tex7.png",
            "Format": 0x6,
            "ImageDataOffset": f"0x{img_off:X}",
            "ImagePayloadLength": 64,
            "ImageDataCapacity": 64,
        }]

        with tempfile.TemporaryDirectory() as tmp:
            # Create a tex/ folder with a dummy PNG so resolve_texture_path finds it.
            model_dir = os.path.join(tmp, "model")
            tex_dir = os.path.join(model_dir, "tex")
            os.makedirs(tex_dir)
            sluggie = os.path.join(model_dir, "model.sluggie")
            open(sluggie, "w").close()
            open(os.path.join(tex_dir, "tex7.png"), "wb").close()

            plan = build_texture_plan(sluggie, descriptors, encoder=fake_encoder)

            dat = bytearray(dat_size)
            dat_path = os.path.join(tmp, "output.dat")
            self._write_file(dat_path, bytes(dat))

            writes = build_texture_writes(descriptors, plan, dat_size, dat_size)

            with open(dat_path, "r+b") as f:
                for write in writes:
                    f.seek(write.offset)
                    f.write(write.bytes)
                    f.seek(write.offset)
                    readback = f.read(write.payload_length)
                    self.assertEqual(readback, write.bytes)

            result = self._read_file(dat_path)

        self.assertEqual(result[img_off : img_off + 64], known_image)

    def test_unpatch_image_only_descriptor(self):
        """Unpatch a descriptor with no palette; only image range is restored."""
        image_bytes = b"\xCC" * 128
        img_off = 0x80
        dat_size = 0x200

        input_dat = bytearray(dat_size)
        input_dat[img_off : img_off + len(image_bytes)] = image_bytes
        input_dat = bytes(input_dat)

        output_dat = bytearray(dat_size)
        output_dat[img_off : img_off + len(image_bytes)] = b"\x00" * len(image_bytes)

        descriptors = [{
            "TextureIndex": 2,
            "ImageDataOffset": f"0x{img_off:X}",
            "ImagePayloadLength": len(image_bytes),
        }]

        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.dat")
            output_path = os.path.join(tmp, "output.dat")
            self._write_file(input_path, input_dat)
            self._write_file(output_path, bytes(output_dat))

            writes = build_unpatch_texture_writes(
                descriptors, input_path, dat_size, dat_size,
            )
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0].kind, "image")

            with open(output_path, "r+b") as f:
                for write in writes:
                    f.seek(write.offset)
                    f.write(write.bytes)
                    f.seek(write.offset)
                    readback = f.read(write.payload_length)
                    self.assertEqual(readback, write.bytes)

            result = self._read_file(output_path)

        self.assertEqual(result[img_off : img_off + len(image_bytes)], image_bytes)


if __name__ == "__main__":
    unittest.main()
