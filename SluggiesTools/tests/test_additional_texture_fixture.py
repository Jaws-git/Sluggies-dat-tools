import copy
import pathlib
import sys
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_additional_texture_fixture as fixture


class PrepareFixtureDataTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "SluggiesModel": {
                "TextureDescriptors": [
                    {
                        "TextureIndex": 0,
                        "TextureFileName": "0.png",
                        "Format": 14,
                        "AdditionalMipCount": 0,
                        "ImageDataOffset": "0x100",
                        "ImageDataLength": 32,
                        "TextureDescriptorOffset": "0x20",
                    }
                ],
                "Submeshes": [
                    {
                        "DisplayStates": [
                            {"DisplayStateId": 1, "ShaderMode": "11110000", "PrimListLength": 0},
                            {"DisplayStateId": 7, "ShaderMode": "Spec", "PrimListLength": 32},
                        ]
                    }
                ],
            }
        }

    def test_appends_descriptor_and_redirects_active_setter(self):
        original = copy.deepcopy(self.source)
        data, descriptor, setter_index = fixture.prepare_fixture_data(
            self.source, "new.png", 0, 1, 0
        )

        self.assertEqual(self.source, original)
        self.assertEqual(descriptor["TextureIndex"], 1)
        self.assertEqual(descriptor["TextureFileName"], "new.png")
        self.assertEqual(setter_index, 0)
        self.assertEqual(
            data["SluggiesModel"]["Submeshes"][0]["DisplayStates"][0]["ShaderModeEdited"],
            "11110001",
        )
        self.assertEqual(
            data["SluggiesModel"]["AdditionalTextureFixture"]["Targets"],
            [{
                "SubmeshIndex": 0,
                "DisplayStateIndex": 1,
                "TextureSetterDisplayStateIndex": 0,
                "OriginalTextureIndex": 0,
                "EffectiveShaderMode": "Spec",
                "TextureLayer": 0,
                "WrapS": 1,
                "WrapT": 1,
            }],
        )
        self.assertEqual(
            data["SluggiesModel"]["AdditionalTextureFixtures"],
            [data["SluggiesModel"]["AdditionalTextureFixture"]],
        )
        self.assertTrue(data["SluggiesModel"]["UseHammerspace"])
        self.assertFalse(data["SluggiesModel"]["ReimportTextures"])

    def test_multiple_targets_share_appended_texture_index(self):
        self.source["SluggiesModel"]["Submeshes"][0]["DisplayStates"].extend([
            {"DisplayStateId": 1, "ShaderMode": "11210000", "PrimListLength": 0},
            {"DisplayStateId": 7, "ShaderMode": "RhSp", "PrimListLength": 32},
        ])
        data, _, _ = fixture.prepare_fixture_data(
            self.source, "new.png", 0, 1, 0, additional_targets=[(0, 3)]
        )

        states = data["SluggiesModel"]["Submeshes"][0]["DisplayStates"]
        self.assertEqual(states[0]["ShaderModeEdited"], "11110001")
        self.assertEqual(states[2]["ShaderModeEdited"], "11210001")
        targets = data["SluggiesModel"]["AdditionalTextureFixture"]["Targets"]
        self.assertEqual([target["OriginalTextureIndex"] for target in targets], [0, 0])
        self.assertEqual([target["EffectiveShaderMode"] for target in targets], ["Spec", "RhSp"])

    def test_rejects_duplicate_targets(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            fixture.prepare_fixture_data(
                self.source, "new.png", 0, 1, 0, additional_targets=[(0, 1)]
            )

    def test_second_append_preserves_both_fixture_entries(self):
        self.source["SluggiesModel"]["Submeshes"][0]["DisplayStates"].extend([
            {"DisplayStateId": 1, "ShaderMode": "11110000", "PrimListLength": 0},
            {"DisplayStateId": 7, "ShaderMode": "RhSp", "PrimListLength": 32},
        ])
        first, _, _ = fixture.prepare_fixture_data(
            self.source, "first.png", 0, 1, 0
        )
        second, descriptor, _ = fixture.prepare_fixture_data(
            first, "second.png", 0, 3, 0
        )

        entries = second["SluggiesModel"]["AdditionalTextureFixtures"]
        self.assertEqual([entry["TextureIndex"] for entry in entries], [1, 2])
        self.assertEqual([entry["TextureFileName"] for entry in entries], ["first.png", "second.png"])
        self.assertEqual(descriptor["TextureIndex"], 2)
        states = second["SluggiesModel"]["Submeshes"][0]["DisplayStates"]
        self.assertEqual(states[0]["ShaderModeEdited"], "11110001")
        self.assertEqual(states[2]["ShaderModeEdited"], "11110002")

    def test_rejects_target_without_primitive_data(self):
        self.source["SluggiesModel"]["Submeshes"][0]["DisplayStates"][1]["PrimListLength"] = 0
        with self.assertRaisesRegex(ValueError, "no primitive data"):
            fixture.prepare_fixture_data(self.source, "new.png", 0, 1, 0)

    def test_rejects_noncontiguous_donor_indices(self):
        self.source["SluggiesModel"]["TextureDescriptors"][0]["TextureIndex"] = 2
        with self.assertRaisesRegex(ValueError, "contiguous"):
            fixture.prepare_fixture_data(self.source, "new.png", 0, 1, 2)


if __name__ == "__main__":
    unittest.main()