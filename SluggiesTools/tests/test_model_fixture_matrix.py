import importlib.util
import json
import pathlib
import struct
import sys
import tempfile
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
SCRIPT_PATH = TOOLS_DIR / "build_model_fixture_matrix.py"
SPEC = importlib.util.spec_from_file_location("build_model_fixture_matrix", SCRIPT_PATH)
fixture_matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture_matrix)


class ModelFixtureMatrixTests(unittest.TestCase):
    def test_default_output_uses_meta_folder(self):
        self.assertEqual(
            fixture_matrix.DEFAULT_OUTPUT_PATH,
            fixture_matrix.PROJECT_DIR / "_docs" / "meta" / "model_replacement_fixture_matrix.json",
        )

    def test_inspect_fixture_records_required_baseline_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            dat_path = temp_path / "dt_na.dat"
            sluggie_path = temp_path / "player_entry00.sluggie"

            model_offset = 0x40
            model_length = 0x200
            pointers = (0x20, 0x80, 0xC0, 0x100, 0, 0x180, 0)
            dat_bytes = bytearray(model_offset + model_length)
            struct.pack_into(">8I", dat_bytes, model_offset, 0, *pointers)
            dat_path.write_bytes(dat_bytes)

            model = {
                "ChunkNumber": 18,
                "FileIndex": 0,
                "ModelOffset": hex(model_offset),
                "ModelLength": model_length,
                "TextureDescriptors": [{}, {}],
                "FacialPoseData": {"ObjectCount": 1},
                "Submeshes": [
                    {
                        "MeshName": "body",
                        "FacesCount": 7,
                        "DisplayStates": [{}, {}],
                        "VertexBuffer": {
                            "VertexBufferLength": 60,
                            "VertexBufferCompCount": 3,
                            "VertexBufferQuantizeInfo": 0x30,
                        },
                    }
                ],
                "SkinData": {"SK1s": [{}], "SK2s": [{}, {}], "SKAccs": [{}]},
            }
            sluggie_path.write_text(json.dumps({"SluggiesModel": model}), encoding="utf-8")

            result = fixture_matrix.inspect_fixture(sluggie_path, dat_path)

            self.assertEqual(result["chunk_number"], 18)
            self.assertEqual(result["file_index"], 0)
            self.assertEqual(result["section_sizes"]["GPL"], 0x60)
            self.assertEqual(result["section_sizes"]["ptr7"], 0x80)
            self.assertEqual(result["vertex_count"], 10)
            self.assertEqual(result["face_count"], 7)
            self.assertEqual(result["submesh_count"], 1)
            self.assertEqual(result["display_state_count"], 2)
            self.assertEqual(result["sk_entry_counts"], {"SK1": 1, "SK2": 2, "SKAcc": 1})
            self.assertEqual(result["texture_count"], 2)
            self.assertEqual(result["nonzero_trailing_pointers"], {"ptr7": "0x180"})
            self.assertEqual(
                result["coverage"],
                ["skinned", "skacc", "multiple_display_states", "recognized_ptr7_facial_pose"],
            )

    def test_build_matrix_preserves_manual_fixture_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            dat_path = temp_path / "dt_na.dat"
            sluggie_path = temp_path / "player_entry00.sluggie"

            dat_bytes = bytearray(0x240)
            struct.pack_into(">8I", dat_bytes, 0x40, 0, 0x20, 0x80, 0xC0, 0x100, 0, 0, 0)
            dat_path.write_bytes(dat_bytes)
            model = {
                "ChunkNumber": 18,
                "FileIndex": 0,
                "ModelOffset": "0x40",
                "ModelLength": 0x200,
                "Submeshes": [],
            }
            sluggie_path.write_text(json.dumps({"SluggiesModel": model}), encoding="utf-8")

            source = fixture_matrix._display_path(sluggie_path)
            existing_matrix = {
                "fixtures": [{
                    "source": source,
                    "manual_control_test": {"character_select": "pass"},
                }],
                "edited_blender_fixtures": {
                    "position_only": {"source": "fixtures/position_only.sluggie", "status": "ready"},
                },
            }
            result = fixture_matrix.build_matrix([sluggie_path], dat_path, existing_matrix)

            self.assertEqual(result["fixtures"][0]["manual_control_test"], {"character_select": "pass"})
            self.assertEqual(
                result["edited_blender_fixtures"]["position_only"],
                {"source": "fixtures/position_only.sluggie", "status": "ready"},
            )


if __name__ == "__main__":
    unittest.main()