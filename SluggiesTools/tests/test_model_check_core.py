"""Check Models rules (BlenderAddonSrc/ModelCheckCore.py), bpy-free."""

import ast
import os
import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(BLENDER_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_ADDON_DIR))

import FieldCodec  # noqa: E402
import ModelCheckCore as core  # noqa: E402


def _hierarchy(*parents):
    """BoneHierarchy with bone i's parent parents[i]."""
    return [{"BoneId": i, "ParentBoneId": p} for i, p in enumerate(parents)]


class TestFindings(unittest.TestCase):
    def test_sort_groups_by_object_and_puts_errors_first(self):
        findings = [
            core.Finding("a", core.INFO, "Mesh", "i", ""),
            core.Finding("b", core.WARNING, "Arm", "w", ""),
            core.Finding("c", core.ERROR, "Mesh", "e", ""),
        ]
        self.assertEqual([f.check_id for f in core.sort_findings(findings)], ["c", "a", "b"])

    def test_summary(self):
        self.assertEqual(core.fix_summary(5, 1, 3), "Fixed 5, failed 1, 3 left")

    def test_plural(self):
        self.assertEqual(core.plural(1, "vert"), "1 vert")
        self.assertEqual(core.plural(12, "vert"), "12 verts")


class TestMatching(unittest.TestCase):
    MODEL = {
        "ChunkNumber": 18, "ModelOffset": "0x4aa5a00",
        "Submeshes": [
            {"VertexBuffer": {"VertexBufferOffset": "0x10", "VertexBufferLength": 120,
                              "VertexBufferCompCount": 6, "VertexBufferQuantizeInfo": 0x40}},
            {"VertexBuffer": {"VertexBufferOffset": "0x20", "VertexBufferLength": 60,
                              "VertexBufferCompCount": 3, "VertexBufferQuantizeInfo": 0x30}},
        ],
    }

    def test_mesh_name(self):
        self.assertEqual(core.submesh_index_from_mesh_name("18_0x4aa5a00_submesh1_edit.001", 18, "0x4aa5a00"), 1)
        self.assertIsNone(core.submesh_index_from_mesh_name("18_0x4aa5a00_submesh1", 19, "0x4aa5a00"))
        self.assertIsNone(core.submesh_index_from_mesh_name("Cube"))

    def test_match_by_name_then_surface_then_vertex_count(self):
        self.assertEqual(core.match_submesh("18_0x4aa5a00_submesh1", 0, set(), self.MODEL), 1)
        self.assertEqual(core.match_submesh("Renamed", 0, {0}, self.MODEL), 0)
        self.assertIsNone(core.match_submesh("Renamed", 0, {0, 1}, self.MODEL))
        # 60 bytes / (3 comps * 2 bytes) = 10 vertices; 120 / (6 * 4) = 5.
        self.assertEqual(core.match_submesh("Renamed", 10, set(), self.MODEL), 1)
        self.assertEqual(core.match_submesh("Renamed", 5, set(), self.MODEL), 0)
        self.assertIsNone(core.match_submesh("Renamed", 7, set(), self.MODEL))

    def test_mesh_props(self):
        props = core.mesh_props_for_submesh(self.MODEL, 1)
        self.assertEqual(set(props), set(core.REQUIRED_PROPS))
        self.assertEqual(props["VertexBufferOffset"], "0x20")
        self.assertEqual(core.submesh_index_by_offset(self.MODEL, "0x20"), 1)

    def test_low_partner(self):
        self.assertTrue(core.is_low_folder("19_L_Mario"))
        self.assertTrue(core.low_partner_matches("19_L_Mario", "18_Mario"))
        self.assertFalse(core.low_partner_matches("19_L_Mario", "20_Luigi"))
        self.assertFalse(core.low_partner_matches("18_Mario", "19_L_Mario"))

    def test_free_file_name(self):
        self.assertEqual(core.free_file_name("a.png", ["b.png"]), "a.png")
        self.assertEqual(core.free_file_name("a.png", ["A.png", "a_1.png"]), "a_2.png")

    def test_template_default(self):
        self.assertEqual(core.default_template(False), "builtin:rigid_spec_v1")
        self.assertEqual(core.default_template(True), "builtin:stadium_shdw_opaque_v1")

    def test_wrap_modes_follow_the_texture_channel(self):
        model = {"Submeshes": [{"UVChannels": [
            {"TextureIndex": 0, "WrapS": 1, "WrapT": 1}, {"TextureIndex": 3, "WrapS": 2, "WrapT": 0}]}]}
        self.assertEqual(core.wrap_modes_for_texture(model, 0, 3), (2, 0))
        self.assertEqual(core.wrap_modes_for_texture(model, 0, 9), (1, 1))


class TestBones(unittest.TestCase):
    def test_donor_bone_problems(self):
        bones = [
            core.BoneInfo("bone_0", None, False),
            core.BoneInfo("bone_1", "bone_0", False),
            core.BoneInfo("Head", "bone_1", False),        # renamed
            core.BoneInfo("bone_3", "bone_0", False),      # parent was bone_1
            core.BoneInfo("bone_5", None, True),           # added, no parent
        ]
        problems = core.donor_bone_problems(bones, _hierarchy(None, 0, 1, 1, 1))
        self.assertEqual(problems["renamed"], ["Head"])
        self.assertEqual(problems["missing"], ["bone_2", "bone_4"])
        self.assertEqual(problems["reparented"], ["bone_3"])
        self.assertEqual(problems["orphan"], ["bone_5"])

    def test_stadium_root_bone_is_no_orphan(self):
        # A new stadium root bone is exported (Dolphin, 2026-10-18).
        bones = [core.BoneInfo("bone_0", None, False), core.BoneInfo("bone_1", None, True)]
        self.assertEqual(core.donor_bone_problems(bones, _hierarchy(None), stadium=True)["orphan"], [])
        self.assertEqual(core.donor_bone_problems(bones, _hierarchy(None))["orphan"], ["bone_1"])

    def test_clean_skeleton(self):
        bones = [core.BoneInfo("bone_0", None, False), core.BoneInfo("bone_1", "bone_0", False)]
        self.assertFalse(any(core.donor_bone_problems(bones, _hierarchy(None, 0)).values()))

    def test_root_bone_scale(self):
        self.assertTrue(core.root_bone_scale_ok([1, 1.5, 1]))
        self.assertFalse(core.root_bone_scale_ok([1, 1]))
        self.assertFalse(core.root_bone_scale_ok("abc"))
        self.assertFalse(core.root_bone_scale_ok([1, float("nan"), 1]))


class TestWeights(unittest.TestCase):
    def test_unweighted_uses_allowed_ancestors(self):
        parent_of = {0: None, 1: 0, 2: 1}
        weights = [{2: 1.0}, {}, {5: 1.0}, {}]
        # bone 2 reaches allowed bone 1; vertex 1 is unweighted; bone 5 is
        # unknown; vertex 3 is not used by any face.
        self.assertEqual(core.unweighted_vertices(weights, {0, 1, 2}, {1}, parent_of), [1, 2])

    def test_unweighted_skips_vertices_the_donor_left_bare(self):
        self.assertEqual(core.unweighted_vertices([{}, {}], {0, 1}, {0}, {}, donor_covered={1}), [1])

    def test_donor_covered_vertices(self):
        raw = struct.pack('>Hf', 0, 1.0) + struct.pack('>Hf', 2, 0.0) + struct.pack('>Hf', 3, 0.5)
        hierarchy = [{"BoneId": 0, "VertexInfluences": [
            {"SubmeshIndex": 0, "Influences": FieldCodec.encode_field(raw, True)},
            {"SubmeshIndex": 1, "Influences": FieldCodec.encode_field(struct.pack('>Hf', 7, 1.0), True)},
        ]}]
        self.assertEqual(core.donor_covered_vertices(hierarchy, 0), {0, 3})

    def test_overweighted_and_limit(self):
        weights = [{i: 0.1 * (i + 1) for i in range(6)}, {0: 1.0}]
        self.assertEqual(core.overweighted_vertices(weights), [0])
        limited = core.limited_weights(weights[0])
        self.assertEqual(sorted(limited), [2, 3, 4, 5])
        self.assertAlmostEqual(sum(limited.values()), 1.0)

    def test_host_bone_vote(self):
        self.assertEqual(core.host_bone_vote([{3}, {3}]), (True, 3))
        self.assertEqual(core.host_bone_vote([{3}, {4}, {4}]), (False, 4))
        self.assertEqual(core.host_bone_vote([{3, 4}]), (False, 3))
        self.assertEqual(core.host_bone_vote([set()]), (False, None))
        self.assertEqual(core.host_bone_vote([]), (False, None))


class TestSizeEstimate(unittest.TestCase):
    def test_payload_matches_the_patcher_table(self):
        # CMPR 64x64: 16 x 16 4x4 blocks of 8 bytes.
        self.assertEqual(core.image_payload_size(64, 64, 0xE), 2048)
        # RGBA8 4x4: one 64-byte block.
        self.assertEqual(core.image_payload_size(4, 4, 0x6), 64)
        # C8 with a 256-entry palette.
        self.assertEqual(core.texture_bytes(8, 4, 0x9, palette_entries=256), 32 + 512)

    def test_mips_and_clamp(self):
        self.assertEqual(core.texture_bytes(16, 16, 0xE, additional_mips=1),
                         core.image_payload_size(16, 16, 0xE) + core.image_payload_size(8, 8, 0xE))
        self.assertEqual(core.texture_bytes(2048, 512, 0xE), core.image_payload_size(1024, 256, 0xE))

    def test_estimate_keeps_unchanged_models_at_their_length(self):
        model = {
            "ModelLength": 100_000,
            "TextureDescriptors": [{"TextureIndex": 0, "TextureFileName": "a.png", "Width": 64,
                                    "Height": 64, "Format": 0xE, "ImageDataCapacity": 2048}],
            "Submeshes": [{"VertexBuffer": {"VertexBufferLength": 60, "VertexBufferCompCount": 3,
                                            "VertexBufferQuantizeInfo": 0x30}, "FacesCount": 4,
                           "UVChannels": [{"UVChannelLength": 40}],
                           "DisplayStates": [{"PrimListLength": 100}]}],
        }
        # 10 vertices, 4 triangles: unchanged adds nothing.
        self.assertEqual(core.estimate_size(model, {0: (10, 4)}, 0, {}, []), (100_000 - 2048, 2048))
        # Twice the vertices and triangles: (60 + 40) + 100 more bytes.
        self.assertEqual(core.estimate_size(model, {0: (20, 8)}, 0, {}, [])[0], 100_000 - 2048 + 200)
        # A bigger PNG and one new texture cloned from descriptor 0.
        _model, textures = core.estimate_size(model, {}, 0, {"a.png": (128, 128)}, [(64, 64, 0)])
        self.assertEqual(textures, 8192 + 2048)

    def test_interleaved_normals_count_once(self):
        submesh = {"VertexBuffer": {"VertexBufferOffset": "0x100", "VertexBufferLength": 120},
                   "NormalBuffer": {"NormalBufferOffset": "0x106", "NormalBufferLength": 120}}
        self.assertEqual(core.donor_mesh_parts(submesh), (120, 0))
        submesh["NormalBuffer"]["NormalBufferOffset"] = "0x400"
        self.assertEqual(core.donor_mesh_parts(submesh), (240, 0))

    def test_size_line(self):
        self.assertEqual(core.size_line(412 * 1024, 256 * 1024),
                         "Estimated size: 668 KB total (model 412 KB + textures 256 KB)")


class TestVisibleCollections(unittest.TestCase):
    def test_hidden_parent_hides_its_children(self):
        tree = ("Scene Collection", False, [
            ("Model", False, [("Parts", False, [])]),
            ("Refs", True, [("Downloads", False, [])]),
        ])
        self.assertEqual(core.visible_collections(tree), {"Scene Collection", "Model", "Parts"})

    def test_linked_twice_is_shown_by_its_visible_place(self):
        tree = ("Scene Collection", False, [
            ("Hidden", True, [("Shared", False, [])]),
            ("Shown", False, [("Shared", False, [])]),
        ])
        self.assertIn("Shared", core.visible_collections(tree))


class TestStaysBpyFree(unittest.TestCase):
    def test_no_bpy_import(self):
        tree = ast.parse((BLENDER_ADDON_DIR / 'ModelCheckCore.py').read_text(encoding='utf-8'))
        imported = {alias.name.split('.')[0] for node in ast.walk(tree)
                    if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split('.')[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse(imported & {'bpy', 'bmesh', 'mathutils'})


class TestWiring(unittest.TestCase):
    def test_exporter_runs_the_checks_before_encoding(self):
        source = (BLENDER_ADDON_DIR / 'ExportSluggies.py').read_text(encoding='utf-8')
        gate = 'ModelCheck.export_errors('
        self.assertIn(gate, source)
        self.assertLess(source.index(gate), source.index('encode_bone_hierarchy_edited(context.selected_objects'))
        self.assertIn('self.report({"INFO"}, size_line)', source)

    def test_dialog_shares_the_exporters_rules(self):
        # Check Models reports what the exporter refuses while encoding,
        # through the exporter's own rule functions.
        source = (BLENDER_ADDON_DIR / 'ModelCheck.py').read_text(encoding='utf-8')
        for rule in ('RigidRebuildExport.vertex_bone_problem(', 'HostBones.custom_submesh_host_errors(',
                     'ex._candidate_rigid_retargets(', 'ex._custom_submesh_template_texture_index(',
                     'StadiumCollisionExport.invisible_model_mesh_error(', 'StadiumCollisionExport.chunk_error(',
                     'RigidRebuildExport.donor_position_format_error('):
            self.assertIn(rule, source)
        checks = source[source.index('CHECKS = ['):]
        for check in ('check_rigid_one_bone', 'check_host_bones', 'check_texture_owner',
                      'check_texture_bindings', 'check_collision', 'check_rebuild_format',
                      'check_mesh_limits'):
            self.assertIn(f'({check}, False)', checks)

    def test_dialog_scans_meshes_outside_any_model(self):
        source = (BLENDER_ADDON_DIR / 'ModelCheck.py').read_text(encoding='utf-8')
        run = source[source.index('def run_checks('):]
        self.assertIn('check_stray_objects(context, scopes)', run[:run.index('return')])
        ui = (BLENDER_ADDON_DIR / 'ModelCheckUI.py').read_text(encoding='utf-8')
        self.assertIn('_run_operator("sluggies.make_submesh")', ui)

    def test_fix_order_starts_with_the_mode_switch(self):
        source = (BLENDER_ADDON_DIR / 'ModelCheck.py').read_text(encoding='utf-8')
        fixes = source[source.index('FIXES = {'):]
        self.assertLess(fixes.index('"object_mode"'), fixes.index('"reparent"'))
        self.assertLess(fixes.index('"material_defaults"'), fixes.index('"renumber_bones"'))

    def test_panel_button_sits_below_the_bones_box(self):
        source = (BLENDER_ADDON_DIR / 'SluggiesToolsPanel.py').read_text(encoding='utf-8')
        draw = source[source.index('class SLUGGIES_PT_tools'):]
        self.assertLess(draw.index('_draw_free_host_bones(box'), draw.index('"sluggies.check_models"'))


class FindSluggieFileTests(unittest.TestCase):
    """SluggiePath.resolve's search (ModelCheckCore.find_sluggie_file)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, *parts):
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')
        return str(path)

    def test_nothing_without_a_name_or_folders(self):
        self.assertEqual(core.find_sluggie_file('', [str(self.root)]), '')
        self.assertEqual(core.find_sluggie_file('a.sluggie', ['', str(self.root / 'missing')]), '')

    def test_folder_itself_before_its_subfolders(self):
        sub = self._file('target', 'x.gpl', 'x.sluggie')
        direct = self._file('target', 'x.sluggie')
        self.assertEqual(core.find_sluggie_file('x.sluggie', [str(self.root / 'target')]), direct)
        os.remove(direct)
        self.assertEqual(core.find_sluggie_file('x.sluggie', [str(self.root / 'target')]), sub)

    def test_folders_in_order(self):
        second = self._file('b', 'x.sluggie')
        first = self._file('a', 'x.sluggie')
        self.assertEqual(core.find_sluggie_file('x.sluggie', [str(self.root / 'a'), str(self.root / 'b')]), first)
        self.assertEqual(core.find_sluggie_file('x.sluggie', [str(self.root / 'b'), str(self.root / 'a')]), second)

    def test_accept_rejects_a_namesake(self):
        wrong = self._file('a', 'x.sluggie')
        right = self._file('b', 'x.sluggie')
        found = core.find_sluggie_file('x.sluggie', [str(self.root / 'a'), str(self.root / 'b')],
                                       accept=lambda p: os.path.abspath(p) != os.path.abspath(wrong))
        self.assertEqual(found, right)

    def test_searches_the_output_assets_tree_of_a_folder(self):
        moved = self._file('2_Output_Assets', '32 Boo', '134010752_teresa.gpl', '134010752_teresa.gpl.sluggie')
        old_folder = self.root / '2_Output_Assets' / 'old' / 'teresa.gpl'   # gone: never created
        self.assertEqual(core.find_sluggie_file('134010752_teresa.gpl.sluggie', [str(old_folder)]), moved)
        # Too deep for the export layout: not found.
        deep = self._file('2_Output_Assets', 'a', 'b', 'c', 'd', 'deep.sluggie')
        self.assertEqual(core.find_sluggie_file('deep.sluggie', [str(old_folder)]), '')
        self.assertTrue(os.path.isfile(deep))

    def test_output_assets_root(self):
        self.assertEqual(core.output_assets_root(os.path.join('x', '2_Output_Assets', 'a', 'b.sluggie')),
                         os.path.join(os.path.abspath('x'), '2_Output_Assets'))
        self.assertIsNone(core.output_assets_root(os.path.join('x', 'y', 'b.sluggie')))


class SluggiePathWiringTests(unittest.TestCase):
    """Every reader of the armature's .sluggie path resolves it (source text)."""

    def test_no_raw_property_reads_outside_sluggie_path(self):
        for name in ('ExportSluggies', 'ImportSluggies', 'ModelCheck', 'SluggiesToolsPanel', 'TextureSlotsUI'):
            source = (BLENDER_ADDON_DIR / f'{name}.py').read_text(encoding='utf-8')
            self.assertNotIn("get('SluggieFilePath')", source, name)
            self.assertNotIn("['SluggieFilePath']", source, name)

    def test_exporter_records_the_written_file(self):
        source = (BLENDER_ADDON_DIR / 'ExportSluggies.py').read_text(encoding='utf-8')
        dump = source.index('FieldCodec.dump_sluggie(data, self.filepath)')
        self.assertIn('SluggiePath.remember(arm_obj, self.filepath)', source[dump:dump + 600])


if __name__ == '__main__':
    unittest.main()
