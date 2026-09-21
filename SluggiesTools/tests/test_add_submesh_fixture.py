import base64
import copy
import json
import pathlib
import struct
import sys
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import build_add_submesh_fixture as fixture_mod

if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import synthetic_donor


def _rigid_submesh(name: str, offset_hex: str) -> dict:
    return {
        "MeshName": name,
        "SubmeshOffset": offset_hex,
        "PositionDataPtrFieldOffset": offset_hex,
        "VertexCountFieldOffset": offset_hex,
        "FacesCount": 1,
        "FacesData": "AAA=",
        "DisplayStates": [
            {
                "SurfaceId": f"{name}_ds0",
                "DisplayStateId": 1,
                "PrimListLength": 32,
                "ShaderMode": "0000",
                "ShaderModeFieldOffset": offset_hex,
                "PrimListPtrFieldOffset": offset_hex,
                "PrimListSizeFieldOffset": offset_hex,
                "PrimListAbsoluteOffset": offset_hex,
                "PrimListData": "AAA=",
            },
        ],
        "VertexBuffer": {
            "VertexBufferOffset": offset_hex,
            "VertexBufferLength": 12,
            "VertexBufferCompCount": 3,
            "VertexBufferQuantizeInfo": 0x3B,
            "VertexBufferData": "AAA=",
        },
        "UVChannels": [
            {
                "UVChannelIndex": 0,
                "PaletteName": "",
                "TextureIndex": 0,
                "UVDataPtrFieldOffset": offset_hex,
                "UVCountFieldOffset": offset_hex,
                "UVChannelOffset": offset_hex,
                "UVChannelCompCount": 2,
                "UVChannelQuantizeInfo": 0x3E,
                "UVFacesData": "AAA=",
                "UVChannelData": "AAA=",
            },
        ],
        "ColorChannels": [],
    }


def _skinned_submesh() -> dict:
    return {
        "MeshName": "body",
        "SubmeshOffset": "0x100",
        "PositionDataPtrFieldOffset": "0x100",
        "VertexCountFieldOffset": "0x100",
        "FacesCount": 1,
        "FacesData": "AAA=",
        "DisplayStates": [],
        "VertexBuffer": {
            "VertexBufferOffset": "0x100",
            "VertexBufferLength": 24,
            "VertexBufferCompCount": 6,
            "VertexBufferQuantizeInfo": 0x39,
            "VertexBufferData": "AAA=",
        },
        "UVChannels": [],
        "ColorChannels": [],
    }


def _bone(bone_id, geo_id_raw, parent_id, geo_field_offset="0x1000"):
    return {
        "BoneId": bone_id,
        "GeoId": 0 if geo_id_raw == 0xFFFF else geo_id_raw,
        "GeoIdRaw": geo_id_raw,
        "GeoIdFieldOffset": geo_field_offset,
        "ParentBoneId": parent_id,
        "Skinned": geo_id_raw == 0xFFFF and parent_id is not None,
        "TrackId": bone_id,
        "Translation": [0.0, 0.0, 0.0],
        "Scale": [1.0, 1.0, 1.0],
        "Quaternion": [0.0, 0.0, 0.0, 1.0],
        "VertexInfluences": [],
    }


def _minimal_model() -> dict:
    """A small synthetic .sluggie payload shaped like Mario entry00:
    submesh0 skinned (body), submesh1 rigid (cap, owned by bone 10),
    submesh2 rigid (head, owned by bone 11). Bone 0 is root (mesh-free,
    not SKN-used). Bone 1 is mesh-free and SKN-used. Bone 2 is mesh-free,
    not SKN-used, and has a parent (the expected default pick).
    """
    return {
        "SluggiesModel": {
            "ChunkNumber": 18,
            "FileIndex": 0,
            "ModelOffset": "0x1000",
            "ModelLength": 0x10000,
            "UseBase64": True,
            "Submeshes": [
                _skinned_submesh(),
                _rigid_submesh("cap", "0x200"),
                _rigid_submesh("head", "0x300"),
            ],
            "BoneHierarchy": [
                _bone(0, 0xFFFF, None),
                _bone(1, 0xFFFF, 0),
                _bone(2, 0xFFFF, 0),
                _bone(10, 5, 0),
                _bone(11, 6, 0),
            ],
            "SkinData": {
                "SK1s": [{"BoneIndex": 1}],
                "SK2s": [],
                "SKAccs": [],
            },
        }
    }


class PrepareFixtureDataTests(unittest.TestCase):
    def test_default_probe_appends_head_clone_and_picks_nonroot_free_bone(self):
        source = _minimal_model()
        original = copy.deepcopy(source)

        result = fixture_mod.prepare_fixture_data(source)
        model = result["SluggiesModel"]

        self.assertEqual(len(model["Submeshes"]), 4)
        clone = model["Submeshes"][3]
        template = model["Submeshes"][2]
        self.assertEqual(clone["MeshName"], "head")
        self.assertEqual(clone["MeshName"], template["MeshName"])
        self.assertEqual(clone["VertexBuffer"]["VertexBufferData"], template["VertexBuffer"]["VertexBufferData"])

        meta = model["AddSubmeshFixture"]
        self.assertEqual(meta["NewSubmeshIndex"], 3)
        self.assertEqual(meta["TemplateSubmeshIndex"], 2)
        # Bone 2 is the lowest-ID, mesh-free, non-SKN, non-root bone.
        self.assertEqual(meta["HostBoneId"], 2)
        self.assertFalse(meta["SknUsedHostBone"])

        host_bone = next(b for b in model["BoneHierarchy"] if b["BoneId"] == 2)
        self.assertEqual(host_bone["GeoIdEdited"], 3)

        self.assertTrue(model["UseHammerspace"])

        # Donor submeshes 0-2 are unchanged.
        for i in range(3):
            self.assertEqual(model["Submeshes"][i], original["SluggiesModel"]["Submeshes"][i])
        # The source dict itself was not mutated (prepare_fixture_data deep-copies).
        self.assertEqual(source, original)

    def test_clone_has_source_layout_fields_cleared(self):
        result = fixture_mod.prepare_fixture_data(_minimal_model())
        clone = result["SluggiesModel"]["Submeshes"][3]
        self.assertEqual(clone["SubmeshOffset"], "0x0")
        self.assertEqual(clone["PositionDataPtrFieldOffset"], "0x0")
        self.assertEqual(clone["VertexBuffer"]["VertexBufferOffset"], "0x0")
        self.assertEqual(clone["UVChannels"][0]["UVDataPtrFieldOffset"], "0x0")
        self.assertIsNone(clone["DisplayStates"][0]["ShaderModeFieldOffset"])

    def test_explicit_host_bone_already_owning_mesh_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(_minimal_model(), host_bone=11)
        self.assertIn("already owns submesh", str(ctx.exception))

    def test_explicit_host_bone_that_drives_skinning_is_rejected_without_flag(self):
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(_minimal_model(), host_bone=1)
        self.assertIn("drives skinning", str(ctx.exception))

    def test_allow_skinned_bone_flag_permits_skn_used_host(self):
        result = fixture_mod.prepare_fixture_data(
            _minimal_model(), host_bone=1, allow_skinned_bone=True,
        )
        meta = result["SluggiesModel"]["AddSubmeshFixture"]
        self.assertEqual(meta["HostBoneId"], 1)
        self.assertTrue(meta["SknUsedHostBone"])

    def test_explicit_host_bone_missing_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(_minimal_model(), host_bone=999)
        self.assertIn("does not exist", str(ctx.exception))

    def test_template_submesh_by_explicit_index(self):
        result = fixture_mod.prepare_fixture_data(_minimal_model(), template_submesh=1)
        meta = result["SluggiesModel"]["AddSubmeshFixture"]
        self.assertEqual(meta["TemplateSubmeshIndex"], 1)
        self.assertEqual(result["SluggiesModel"]["Submeshes"][3]["MeshName"], "cap")

    def test_non_rigid_template_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(_minimal_model(), template_submesh=0)
        self.assertIn("not rigid", str(ctx.exception))

    def test_unknown_template_name_is_rejected(self):
        with self.assertRaises(ValueError):
            fixture_mod.prepare_fixture_data(_minimal_model(), template_submesh="tail")

    def test_no_eligible_bone_raises_clear_error(self):
        data = _minimal_model()
        model = data["SluggiesModel"]
        # Make every mesh-free bone SKN-used.
        model["SkinData"]["SK1s"] = [{"BoneIndex": 0}, {"BoneIndex": 1}, {"BoneIndex": 2}]
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(data)
        self.assertIn("no eligible host bone", str(ctx.exception))

    def test_root_bone_used_only_as_last_resort(self):
        data = _minimal_model()
        model = data["SluggiesModel"]
        # Remove every non-root, non-SKN, mesh-free candidate (bone 2), so
        # only the root bone (0) qualifies.
        model["BoneHierarchy"] = [b for b in model["BoneHierarchy"] if b["BoneId"] != 2]
        model["SkinData"]["SK1s"] = [{"BoneIndex": 1}]
        result = fixture_mod.prepare_fixture_data(data)
        self.assertEqual(result["SluggiesModel"]["AddSubmeshFixture"]["HostBoneId"], 0)


def _b64_int16(values: list[int]) -> str:
    return base64.b64encode(struct.pack(f">{len(values)}h", *values)).decode("ascii")


class OrderProbeTests(unittest.TestCase):
    def test_position_scale_scales_only_the_clone(self):
        data = _minimal_model()
        head = data["SluggiesModel"]["Submeshes"][2]
        head["VertexBuffer"]["VertexBufferData"] = _b64_int16([100, -200, 6, 30000, -30000, 0])
        head["VertexBuffer"]["VertexBufferDataEdited"] = head["VertexBuffer"]["VertexBufferData"]

        result = fixture_mod.prepare_fixture_data(data, position_scale=0.5)
        model = result["SluggiesModel"]
        clone_vb = model["Submeshes"][3]["VertexBuffer"]
        template_vb = model["Submeshes"][2]["VertexBuffer"]

        self.assertEqual(
            struct.unpack(">6h", base64.b64decode(clone_vb["VertexBufferData"])),
            (50, -100, 3, 15000, -15000, 0),
        )
        self.assertNotIn("VertexBufferDataEdited", clone_vb)
        # Donor edits are stripped, so the template keeps only its donor data.
        self.assertNotIn("VertexBufferDataEdited", template_vb)
        self.assertEqual(template_vb["VertexBufferData"], head["VertexBuffer"]["VertexBufferData"])
        self.assertEqual(model["AddSubmeshFixture"]["PositionScale"], 0.5)

    def test_prepare_strips_donor_edits_before_appending(self):
        data = _minimal_model()
        body_vb = data["SluggiesModel"]["Submeshes"][0]["VertexBuffer"]
        body_vb["VertexBufferDataEdited"] = body_vb["VertexBufferData"]
        data["SluggiesModel"]["RootBoneScaleEdited"] = [1.0, 1.0, 1.0]

        model = fixture_mod.prepare_fixture_data(data)["SluggiesModel"]

        self.assertNotIn("VertexBufferDataEdited", model["Submeshes"][0]["VertexBuffer"])
        self.assertNotIn("RootBoneScaleEdited", model)
        # The host-bone ownership edit is added after stripping.
        host = model["AddSubmeshFixture"]["HostBoneId"]
        bone = next(b for b in model["BoneHierarchy"] if int(b["BoneId"]) == host)
        self.assertEqual(bone["GeoIdEdited"], model["AddSubmeshFixture"]["NewSubmeshIndex"])

    def test_require_built_submesh_count_rejects_missing_append(self):
        class _Build:
            validation_report = {"validator_facts": {"gpl_submesh_layout": [{}, {}, {}]}}

        fixture_mod.require_built_submesh_count(_Build(), 3)
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.require_built_submesh_count(_Build(), 4)
        self.assertIn("was not built", str(ctx.exception))

    def test_position_scale_rejects_int16_overflow(self):
        data = _minimal_model()
        data["SluggiesModel"]["Submeshes"][2]["VertexBuffer"]["VertexBufferData"] = _b64_int16([20000, 0, 0])
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(data, position_scale=2.0)
        self.assertIn("outside int16", str(ctx.exception))

    def test_position_scale_rejects_non_int16_positions(self):
        data = _minimal_model()
        vb = data["SluggiesModel"]["Submeshes"][2]["VertexBuffer"]
        vb["VertexBufferData"] = base64.b64encode(struct.pack(">3f", 1.0, 2.0, 3.0)).decode("ascii")
        vb["VertexBufferQuantizeInfo"] = 0x40
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(data, position_scale=0.5)
        self.assertIn("int16", str(ctx.exception))

    def test_position_scale_must_be_positive(self):
        with self.assertRaises(ValueError):
            fixture_mod.prepare_fixture_data(_minimal_model(), position_scale=0.0)

    def test_order_broken_when_host_bone_is_below_an_existing_owner(self):
        meta = fixture_mod.prepare_fixture_data(_minimal_model(), host_bone=2)["SluggiesModel"]["AddSubmeshFixture"]
        self.assertEqual(meta["ExistingOwnerBones"], {"5": 10, "6": 11})
        self.assertTrue(meta["OrderBroken"])

    def test_order_kept_when_host_bone_is_above_every_owner(self):
        data = _minimal_model()
        data["SluggiesModel"]["BoneHierarchy"].append(_bone(20, 0xFFFF, 0))
        meta = fixture_mod.prepare_fixture_data(data, host_bone=20)["SluggiesModel"]["AddSubmeshFixture"]
        self.assertFalse(meta["OrderBroken"])


def _state(state_id: int, mode: str, prim_length: int = 0, face_count: int = 0) -> dict:
    return {
        "SurfaceId": f"t_ds{state_id}_{mode}",
        "DisplayStateId": state_id,
        "DisplayStateParamBytes": "000000",
        "ShaderMode": mode,
        "ShaderModeFieldOffset": "0x300",
        "PrimListPtrFieldOffset": "0x300",
        "PrimListSizeFieldOffset": "0x300",
        "PrimListAbsoluteOffset": "0x300",
        "PrimListLength": prim_length,
        "PrimListData": "AAA=" if prim_length else "",
        "FaceCount": face_count,
    }


def _cube_template_model() -> dict:
    data = _minimal_model()
    head = data["SluggiesModel"]["Submeshes"][2]
    head["VertexBuffer"]["VertexBufferData"] = _b64_int16([0, 0, 0])
    head["NormalBuffer"] = {
        "NormalBufferCompCount": 3,
        "NormalBufferQuantizeInfo": 0x3E,
        "NormalBufferData": _b64_int16([0, 0, 16384]),
        "NormalFacesData": "AAA=",
    }
    second_uv = copy.deepcopy(head["UVChannels"][0])
    second_uv["UVChannelIndex"] = 1
    head["UVChannels"].append(second_uv)
    head["ColorChannels"] = [{
        "ColorChannelIndex": 0,
        "ColorChannelCompCount": 4,
        "ColorChannelQuantizeInfo": 0x30,
        "ColorChannelData": "/////w==",
        "ColorFacesData": "AAA=",
    }]
    head["FaceTextureIndices"] = base64.b64encode(struct.pack(">4H", 2, 2, 1, 1)).decode("ascii")
    head["DisplayStates"] = [
        _state(1, "11110002"),
        _state(3, "00003cbc"),
        _state(7, "Spec", prim_length=32, face_count=2),
        _state(1, "11110001", prim_length=64, face_count=2),
    ]
    return data


def _cube_clone(**options) -> tuple[dict, dict]:
    result = fixture_mod.prepare_fixture_data(_cube_template_model(), host_bone=2, cube_half_extent=0.1, **options)
    model = result["SluggiesModel"]
    return model["Submeshes"][3], model["AddSubmeshFixture"]


class CubeProbeTests(unittest.TestCase):
    def test_type3_layout_matches_mario_head_stream_layout(self):
        self.assertEqual(
            fixture_mod._type3_descriptors(0x3CBC),
            [
                {"key": "position", "index_size": 2},
                {"key": "lighting", "index_size": 2},
                {"key": "color0", "index_size": 1},
                {"key": "texture0", "index_size": 2},
                {"key": "texture1", "index_size": 2},
            ],
        )

    def test_cube_goes_into_the_largest_surface_and_empties_the_others(self):
        clone, meta = _cube_clone()
        cube = meta["Cube"]
        self.assertEqual(cube["DisplayStateIndex"], 3)
        self.assertEqual(cube["FaceTextureIndex"], 1)
        self.assertEqual(cube["QuantizedHalfExtent"], 205)
        states = clone["DisplayStates"]
        self.assertEqual((states[2]["PrimListData"], states[2]["PrimListLength"], states[2]["FaceCount"]), ("", 0, 0))
        self.assertEqual(states[3]["FaceCount"], 12)
        self.assertEqual(states[3]["PrimListLength"] % 32, 0)
        self.assertEqual(len(base64.b64decode(states[3]["PrimListData"])), states[3]["PrimListLength"])
        self.assertFalse(any("PrimListDataEdited" in state for state in states))
        self.assertEqual(clone["FacesCount"], 12)
        self.assertEqual(struct.unpack(">12H", base64.b64decode(clone["FaceTextureIndices"])), (1,) * 12)

    def test_cube_triangles_are_closed_counter_clockwise_and_in_range(self):
        clone, meta = _cube_clone()
        layout = meta["Cube"]["VertexStreamLayout"]
        state = clone["DisplayStates"][3]
        faces = fixture_mod.drawlist.decodeDrawList(base64.b64decode(state["PrimListData"]), layout)
        self.assertEqual(len(faces), 12)

        raw_positions = struct.unpack(">24h", base64.b64decode(clone["VertexBuffer"]["VertexBufferData"]))
        positions = [raw_positions[i:i + 3] for i in range(0, 24, 3)]
        self.assertEqual({abs(value) for value in raw_positions}, {205})
        self.assertEqual(len(set(positions)), 8)
        raw_normals = struct.unpack(">18h", base64.b64decode(clone["NormalBuffer"]["NormalBufferData"]))
        normals = [raw_normals[i:i + 3] for i in range(0, 18, 3)]
        self.assertEqual(sorted(normals), sorted([
            (16384, 0, 0), (-16384, 0, 0), (0, 16384, 0), (0, -16384, 0), (0, 0, 16384), (0, 0, -16384),
        ]))
        for channel in clone["UVChannels"]:
            self.assertEqual(
                struct.unpack(">8h", base64.b64decode(channel["UVChannelData"])),
                (0, 0, 16384, 0, 16384, 16384, 0, 16384),
            )

        edges = {}
        for face in faces:
            normal_index = face[0]["lighting"]
            self.assertTrue(all(vertex["lighting"] == normal_index for vertex in face))
            self.assertTrue(all(vertex["color0"] == 0 for vertex in face))
            self.assertTrue(all(vertex["texture0"] == vertex["texture1"] < 4 for vertex in face))
            a, b, c = (positions[vertex["position"]] for vertex in face)
            u = [b[k] - a[k] for k in range(3)]
            w = [c[k] - a[k] for k in range(3)]
            cross = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            self.assertGreater(sum(cross[k] * normals[normal_index][k] for k in range(3)), 0)
            corners = [vertex["position"] for vertex in face]
            for start, end in zip(corners, corners[1:] + corners[:1]):
                edges[(start, end)] = edges.get((start, end), 0) + 1
        # Closed, consistently wound surface: every directed edge appears once
        # and its reverse appears once.
        self.assertTrue(all(count == 1 for count in edges.values()))
        self.assertTrue(all((end, start) in edges for start, end in edges))

        self.assertEqual(
            struct.unpack(">36H", base64.b64decode(clone["FacesData"])),
            tuple(vertex["position"] for face in faces for vertex in face),
        )

    def test_explicit_display_state_must_draw_primitives(self):
        with self.assertRaises(ValueError) as ctx:
            _cube_clone(cube_display_state=0)
        self.assertIn("does not draw primitives", str(ctx.exception))
        clone, meta = _cube_clone(cube_display_state=2)
        self.assertEqual(meta["Cube"]["FaceTextureIndex"], 2)
        self.assertEqual(clone["DisplayStates"][3]["PrimListLength"], 0)

    def test_layout_needing_a_missing_uv_channel_is_rejected(self):
        data = _cube_template_model()
        data["SluggiesModel"]["Submeshes"][2]["UVChannels"].pop()
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(data, host_bone=2, cube_half_extent=0.1)
        self.assertIn("texture1", str(ctx.exception))

    def test_missing_type3_layout_is_rejected(self):
        data = _cube_template_model()
        states = data["SluggiesModel"]["Submeshes"][2]["DisplayStates"]
        del states[1]
        with self.assertRaises(ValueError) as ctx:
            fixture_mod.prepare_fixture_data(data, host_bone=2, cube_half_extent=0.1)
        self.assertIn("no Type-3", str(ctx.exception))

    def test_cube_options_are_validated(self):
        with self.assertRaises(ValueError):
            fixture_mod.prepare_fixture_data(_cube_template_model(), cube_half_extent=0.1, position_scale=0.5)
        with self.assertRaises(ValueError):
            fixture_mod.prepare_fixture_data(_cube_template_model(), cube_display_state=3)
        with self.assertRaises(ValueError):
            fixture_mod.prepare_fixture_data(_cube_template_model(), cube_half_extent=0.0)


def _synthetic_block(tex_offset: int, image_offsets: list[int], palette_offsets: list[int]) -> bytes:
    """Model header + TEX section with the given TEX-relative payload pointers."""
    size = tex_offset + 4 + 0x20 * len(image_offsets) + 0x40
    block = bytearray(size)
    struct.pack_into(">I", block, 0x04, 0x20)        # GPL
    struct.pack_into(">I", block, 0x0C, tex_offset)  # TEX
    struct.pack_into(">H", block, tex_offset, len(image_offsets))
    for index, (image, palette) in enumerate(zip(image_offsets, palette_offsets)):
        struct.pack_into(">II", block, tex_offset + 4 + index * 0x20, image, palette)
    return bytes(block)


class SectionAlignmentFactsTests(unittest.TestCase):
    def test_all_aligned_block_reports_nothing_misaligned(self):
        facts = fixture_mod.section_alignment_facts(_synthetic_block(0x40, [0x20, 0x60], [0, 0x80]))
        self.assertEqual(facts["misaligned"], [])
        self.assertEqual(facts["sections"], {"GPL": 0x20, "TEX": 0x40})
        self.assertEqual([t["image"] for t in facts["textures"]], [0x60, 0xA0])
        self.assertEqual([t["palette"] for t in facts["textures"]], [None, 0xC0])

    def test_tex_section_off_boundary_flags_section_and_every_payload(self):
        # TEX at 8 mod 32 with TEX-relative payloads at 0 mod 32: the shape the
        # full GPL serializer produced for every Phase 0 in-game fixture.
        facts = fixture_mod.section_alignment_facts(_synthetic_block(0x48, [0x20, 0x40], [0, 0]))
        self.assertEqual(
            facts["misaligned"],
            ["TEX section at +0x48", "texture 0 image at +0x68", "texture 1 image at +0x88"],
        )

    def test_short_block_is_rejected(self):
        with self.assertRaises(ValueError):
            fixture_mod.section_alignment_facts(b"\x00" * 8)


class BuildFixtureSyntheticDonorTests(unittest.TestCase):
    """End-to-end smoke test over the synthetic donor (see synthetic_donor.py
    for why this does not read a real export)."""

    def setUp(self):
        self.env = self.enterContext(synthetic_donor.donor_environment())
        self.donor_submesh_count = len(self.env.data['SluggiesModel']['Submeshes'])

    def test_build_fixture_validates_and_preserves_donor_submeshes(self):
        source_data = self.env.reload()

        prepared = fixture_mod.prepare_fixture_data(source_data)
        prepared_submeshes = prepared["SluggiesModel"]["Submeshes"]
        stripped_source = copy.deepcopy(source_data)
        fixture_mod.strip_donor_edits(stripped_source)
        donor_submeshes = stripped_source["SluggiesModel"]["Submeshes"]
        self.assertEqual(len(prepared_submeshes), len(donor_submeshes) + 1)
        for i in range(len(donor_submeshes)):
            self.assertEqual(prepared_submeshes[i], donor_submeshes[i])

        fixture_path = self.env.directory / '_test_add_submesh_probe1.sluggie'
        build, output_offset = fixture_mod.build_fixture(
            self.env.sluggie_path, fixture_path,
            host_bone=None, template_submesh="head",
            allow_skinned_bone=False, write=False,
        )

        self.assertTrue(build.validation_report["valid"], build.validation_report.get("errors"))
        self.assertIsNone(output_offset)
        facts = build.validation_report["validator_facts"]
        self.assertEqual(len(facts["gpl_submesh_layout"]), len(donor_submeshes) + 1)

    def _build_donor(self, name: str, **options):
        build, _ = fixture_mod.build_fixture(
            self.env.sluggie_path, self.env.directory / name,
            host_bone=None, template_submesh="head",
            allow_skinned_bone=False, write=False, **options,
        )
        return build

    def test_default_build_puts_every_section_and_texture_on_32_bytes(self):
        # Phase 2 step 4: HammerspaceMain.BuildHEADERModelBlock now pads every
        # section start to a 32-byte boundary itself (F10), so a plain build
        # (no fixture-local workaround) already comes out aligned.
        build = self._build_donor('_test_add_submesh_aligned.sluggie')
        report = build.validation_report
        self.assertTrue(report["valid"], report.get("errors"))
        alignment = report["section_alignment"]
        self.assertEqual(alignment["misaligned"], [])
        self.assertTrue(alignment["textures"])
        self.assertEqual(
            len(report["validator_facts"]["gpl_submesh_layout"]),
            self.donor_submesh_count + 1,
        )

    def test_order_probe_build_carries_half_size_clone_positions(self):
        host_bone = synthetic_donor.RIGID_OWNER_BONE - 1
        meta = fixture_mod.prepare_fixture_data(
            self.env.reload(), host_bone=host_bone, position_scale=0.5,
        )["SluggiesModel"]["AddSubmeshFixture"]
        # The donor's rigid submesh is owned by a later bone than the host, so
        # hosting the new submesh here breaks the vanilla ascending owner
        # order (F3).
        self.assertEqual(
            meta["ExistingOwnerBones"],
            {str(synthetic_donor.RIGID_OWNER_SUBMESH): synthetic_donor.RIGID_OWNER_BONE},
        )
        self.assertTrue(meta["OrderBroken"])

        build = self._build_donor(
            '_test_add_submesh_order_probe.sluggie',
            position_scale=0.5,
        )
        report = build.validation_report
        self.assertTrue(report["valid"], report.get("errors"))
        self.assertEqual(report["section_alignment"]["misaligned"], [])
        prefix = int(report.get("container_prefix_size", 0))
        inner = int(report.get("inner_assembled_size", len(build.block) - prefix))
        block = build.block[prefix:prefix + inner]
        head = _gpl_positions(block, self.donor_submesh_count - 1)
        clone = _gpl_positions(block, self.donor_submesh_count)
        self.assertEqual(len(clone), len(head))
        self.assertNotEqual(clone, head)
        self.assertEqual(clone, tuple(round(value * 0.5) for value in head))

    def test_cube_probe_build_validates_with_eight_cube_corners(self):
        build = self._build_donor(
            '_test_add_submesh_cube_probe.sluggie',
            cube_half_extent=0.1,
        )
        report = build.validation_report
        self.assertTrue(report["valid"], report.get("errors"))
        self.assertEqual(report["section_alignment"]["misaligned"], [])
        prefix = int(report.get("container_prefix_size", 0))
        inner = int(report.get("inner_assembled_size", len(build.block) - prefix))
        cube = _gpl_positions(build.block[prefix:prefix + inner], self.donor_submesh_count)
        self.assertEqual(len(cube), 24)
        self.assertEqual({abs(value) for value in cube}, {205})


def _gpl_positions(block: bytes, submesh_index: int) -> tuple[int, ...]:
    """Decode one submesh's int16 xyz positions from an assembled model block."""
    u32 = lambda offset: struct.unpack_from(">I", block, offset)[0]
    gpl = u32(0x04)
    layout = gpl + u32(gpl + u32(gpl + 0x10) + submesh_index * 8)
    header = layout + u32(layout)
    data_rel = u32(header)
    count = struct.unpack_from(">H", block, header + 4)[0]
    comp_count = block[header + 7]
    return struct.unpack_from(f">{count * comp_count}h", block, layout + data_rel)


if __name__ == '__main__':
    unittest.main()
