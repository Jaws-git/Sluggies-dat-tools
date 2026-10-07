"""Automatic in-place / Hammerspace choice of the Blender exporter.

The decisions live in the bpy-free BlenderAddonSrc/ExportMode.py and are
imported directly; the wiring in ExportSluggies.py is checked with the AST.
"""

import ast
import base64
import os
import pathlib
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
TOOLS_DIR = ROOT / 'SluggiesTools'
EXPORTER_PATH = BLENDER_ADDON_DIR / 'ExportSluggies.py'
for path in (BLENDER_ADDON_DIR, TOOLS_DIR, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ExportMode  # noqa: E402
import texture_helper  # noqa: E402
from SluggiesTools.Hammerspace import UntanglePolicy  # noqa: E402


def _b64(raw):
    return base64.b64encode(raw).decode('ascii')


def _faces(*triples):
    flat = [i for tri in triples for i in tri]
    return _b64(struct.pack(f'>{len(flat)}H', *flat))


def _write_png(path, width, height):
    # Only the signature and IHDR are read.
    ihdr = struct.pack('>II5B', width, height, 8, 6, 0, 0, 0)
    with open(path, 'wb') as handle:
        handle.write(b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + ihdr + b'\0\0\0\0')


class MirrorTests(unittest.TestCase):
    def test_unused_chunks_match_untangle_policy(self):
        self.assertEqual(tuple(ExportMode.UNUSED_CHARACTER_CHUNKS),
                         tuple(UntanglePolicy.UNUSED_CHARACTER_DIRS))

    def test_clamp_matches_texture_helper(self):
        self.assertEqual(ExportMode.GX_MAX_TEXTURE_DIMENSION,
                         texture_helper.GX_MAX_TEXTURE_DIMENSION)
        for size in ((64, 32), (1024, 1024), (2048, 512), (3000, 1000), (8192, 1)):
            self.assertEqual(ExportMode.clamp_texture_dimensions(*size),
                             texture_helper.clamp_texture_dimensions(*size), size)

    def test_color_entry_sizes_match_binfmt(self):
        import binfmt
        for nibble in range(6):
            self.assertEqual(ExportMode._COLOR_ENTRY_SIZE[nibble],
                             binfmt.color_entry_size(nibble << 4), nibble)


class TextureSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_png_dimensions(self):
        path = os.path.join(self.tmp.name, 'a.png')
        _write_png(path, 128, 64)
        self.assertEqual(ExportMode.png_dimensions(path), (128, 64))
        self.assertIsNone(ExportMode.png_dimensions(os.path.join(self.tmp.name, 'missing.png')))
        bad = os.path.join(self.tmp.name, 'bad.png')
        with open(bad, 'wb') as handle:
            handle.write(b'not a png at all, but long enough')
        self.assertIsNone(ExportMode.png_dimensions(bad))

    def test_only_resized_textures_are_named(self):
        _write_png(os.path.join(self.tmp.name, '0.png'), 128, 128)
        _write_png(os.path.join(self.tmp.name, '1.png'), 256, 128)
        _write_png(os.path.join(self.tmp.name, '2.png'), 2048, 2048)  # clamps to 1024
        descriptors = [
            {'TextureFileName': '0.png', 'Width': 128, 'Height': 128},
            {'TextureFileName': '1.png', 'Width': 128, 'Height': 128},
            {'TextureFileName': '2.png', 'Width': 1024, 'Height': 1024},
            {'TextureFileName': 'gone.png', 'Width': 8, 'Height': 8},
            {'Width': 8, 'Height': 8},
        ]
        self.assertEqual(ExportMode.texture_size_changes(descriptors, self.tmp.name), ['1.png'])
        self.assertEqual(ExportMode.texture_size_changes(descriptors, None), [])


def _submesh(offset, **fields):
    submesh = {'VertexBuffer': {'VertexBufferOffset': offset}}
    submesh.update(fields)
    return submesh


class StaleEditTests(unittest.TestCase):
    def test_hammerspace_fields_outside_the_export_are_reported(self):
        submeshes = [
            _submesh('0x10', FacesDataEdited='AAAA'),               # exported: ignored
            _submesh('0x20'),
            _submesh('0x30', FaceSurfaceIdsEdited='AAAA'),
            _submesh('0x40', UVChannels=[{'UVFacesDataEdited': 'AAAA'}]),
            _submesh('0x50', ColorChannels=[{'ColorChannelDataEdited': 'AAAA'}]),
            _submesh('0x60', UVChannels=[{'UVChannelDataEdited': 'AAAA'}]),  # in-place UV
        ]
        self.assertEqual(
            ExportMode.stale_hammerspace_submeshes(submeshes, ['0x10']),
            ['submesh 2', 'submesh 3', 'submesh 4'],
        )

    def test_in_place_uv_edits_are_promoted_outside_the_export(self):
        submeshes = [
            _submesh('0x10', UVChannels=[{'UVChannelDataEdited': 'E', 'UVFacesData': 'F'}]),
            _submesh('0x20', UVChannels=[
                {'UVChannelDataEdited': 'E', 'UVFacesData': 'F'},
                {'UVChannelDataEdited': 'E', 'UVFacesData': 'F', 'UVFacesDataEdited': 'G'},
                {'UVFacesData': 'F'},
            ]),
        ]
        self.assertEqual(ExportMode.promote_inplace_uv_edits(submeshes, ['0x10']), 1)
        self.assertNotIn('UVFacesDataEdited', submeshes[0]['UVChannels'][0])
        channels = submeshes[1]['UVChannels']
        self.assertEqual(channels[0]['UVFacesDataEdited'], 'F')
        self.assertEqual(channels[1]['UVFacesDataEdited'], 'G')
        self.assertNotIn('UVFacesDataEdited', channels[2])


class ModelLevelReasonTests(unittest.TestCase):
    def test_plain_model_has_no_reason(self):
        self.assertEqual(ExportMode.model_level_reasons({'ChunkNumber': 18}), [])

    def test_each_condition_gives_a_reason(self):
        cases = {
            'custom submeshes (Hat)': dict(custom_submesh_names=['Hat']),
            'bones added with Add Bone': dict(has_new_bones=True),
            'texture changes on materials (body)': dict(changed_materials=['body']),
            'resized textures (0.png)': dict(resized_textures=['0.png']),
            'parts not in this export (submesh 2)': dict(stale_submeshes=['submesh 2']),
        }
        for expected, kwargs in cases.items():
            with self.subTest(expected=expected):
                reasons = ExportMode.model_level_reasons({'ChunkNumber': 18}, **kwargs)
                self.assertEqual(len(reasons), 1)
                self.assertIn(expected, reasons[0])

    def test_unused_character(self):
        for chunk in UntanglePolicy.UNUSED_CHARACTER_DIRS:
            self.assertEqual(len(ExportMode.model_level_reasons({'ChunkNumber': chunk})), 1)
        self.assertFalse(ExportMode.is_unused_character_chunk(88))
        self.assertFalse(ExportMode.is_unused_character_chunk(None))

    def test_slot_export(self):
        """A slot export's donor is its own embedded block: only Hammerspace can patch it."""
        reasons = ExportMode.model_level_reasons({'ChunkNumber': 18, 'DonorEntry': {'Offset': '0x0', 'Data': ''}})
        self.assertEqual(len(reasons), 1)
        self.assertIn('slot export', reasons[0])
        self.assertEqual(ExportMode.DONOR_ENTRY_KEY, 'DonorEntry')     # HammerspaceMain.DONOR_ENTRY_KEY

    def test_mode_message(self):
        self.assertIn('in-place', ExportMode.mode_message([]))
        message = ExportMode.mode_message(['a', 'b'])
        self.assertIn('Hammerspace', message)
        self.assertIn('a; b', message)


class TopologyTests(unittest.TestCase):
    DONOR = _faces((0, 1, 2), (2, 1, 3))

    def test_unchanged(self):
        self.assertFalse(ExportMode.topology_changed([(0, 1, 2), (2, 1, 3)], self.DONOR))

    def test_compressed_donor_field(self):
        raw = base64.b64decode(self.DONOR)
        packed = 'z:' + _b64(zlib.compress(raw))
        self.assertFalse(ExportMode.topology_changed([(0, 1, 2), (2, 1, 3)], packed))

    def test_changes(self):
        for polygons in (
            [(0, 1, 2)],                       # face removed
            [(0, 1, 2), (2, 1, 3), (0, 2, 3)],  # face added
            [(0, 2, 1), (2, 1, 3)],            # winding flipped
            [(0, 1, 2, 3)],                    # quad
            [(0, 1, 2), (2, 1, 4)],            # other vertex
        ):
            with self.subTest(polygons=polygons):
                self.assertTrue(ExportMode.topology_changed(polygons, self.DONOR))


class ColorTests(unittest.TestCase):
    # RGBA4444 (2 bytes): two donor colours shared by three loops.
    CHANNEL = {
        'ColorChannelQuantizeInfo': 0x30,
        'ColorChannelData': _b64(b'\x12\x34\xAB\xCD'),
        'ColorFacesData': _faces((0, 1, 0)),
    }

    def test_unchanged(self):
        self.assertFalse(ExportMode.colors_changed(b'\x12\x34\xAB\xCD\x12\x34', self.CHANNEL))

    def test_changed_value(self):
        self.assertTrue(ExportMode.colors_changed(b'\x12\x34\xAB\xCD\x12\x35', self.CHANNEL))

    def test_changed_loop_count(self):
        self.assertTrue(ExportMode.colors_changed(b'\x12\x34\xAB\xCD', self.CHANNEL))

    def test_rgb8_entry_size(self):
        channel = {
            'ColorChannelQuantizeInfo': 0x10,
            'ColorChannelData': _b64(b'\x01\x02\x03'),
            'ColorFacesData': _faces((0, 0, 0)),
        }
        self.assertFalse(ExportMode.colors_changed(b'\x01\x02\x03' * 3, channel))


class SkinMembershipTests(unittest.TestCase):
    # Vertex size 12 (s16 positions + normals): global index = byte offset // 12.
    SKIN = {
        'SK1s': [{'BoneIndex': 3, 'VertexCnt': 2, 'GplVertexArrValue': 0, 'VertexOffset': 0}],
        'SK2s': [{'BoneIndex1': 3, 'BoneIndex2': 4, 'VertexCnt': 1,
                  'GplVertexArrValue': 24, 'VertexOffset': 0}],
        'SKAccs': [{'BoneIndex': 5, 'GplDestArrValue': 12,
                    'DestIndexData': _b64(struct.pack('>2H', 0, 1))}],
    }

    def donor(self):
        return ExportMode.donor_skin_bone_sets(self.SKIN, 12)

    def test_donor_sets(self):
        self.assertEqual(self.donor(), {0: {3}, 1: {3, 5}, 2: {3, 4, 5}})

    def test_unchanged(self):
        self.assertFalse(ExportMode.skin_membership_changed(
            self.donor(), {0: {3}, 1: {3, 5}, 2: {3, 4, 5}}))

    def test_vertices_outside_the_export_are_ignored(self):
        self.assertFalse(ExportMode.skin_membership_changed(self.donor(), {0: {3}}))

    def test_bone_added_removed_or_moved(self):
        for edited in ({0: {3, 4}}, {1: {3}}, {0: {6}}, {0: set()}, {9: {3}}):
            with self.subTest(edited=edited):
                self.assertTrue(ExportMode.skin_membership_changed(self.donor(), edited))


def _export_class():
    tree = ast.parse(EXPORTER_PATH.read_text(encoding='utf-8'))
    return next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == 'SLUGGIES_OT_export')


def _execute_source():
    cls = _export_class()
    return ast.unparse(next(node for node in cls.body
                            if isinstance(node, ast.FunctionDef) and node.name == 'execute'))


class ExporterWiringTests(unittest.TestCase):
    def test_hammerspace_checkbox_is_gone(self):
        cls = _export_class()
        props = {
            node.target.id for node in cls.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        self.assertNotIn('use_hammerspace', props)
        self.assertIn('reimport_textures', props)
        self.assertNotIn('self.use_hammerspace', _execute_source())

    def test_model_level_reasons_decide_before_meshes_are_encoded(self):
        source = _execute_source()
        decide = 'ExportMode.model_level_reasons('
        self.assertIn(decide, source)
        self.assertLess(source.index(decide), source.index('encode_mesh_hammerspace('))
        self.assertLess(source.index('encode_custom_submesh('), source.index(decide))

    def test_in_place_attempt_runs_on_a_copy(self):
        source = _execute_source()
        self.assertIn('data = base_data if use_hammerspace else copy.deepcopy(base_data)', source)

    def test_in_place_skin_encoder_runs_only_without_mesh_reasons(self):
        source = _execute_source()
        switch = 'if not use_hammerspace and pass_reasons:'
        self.assertIn(switch, source)
        self.assertLess(source.index(switch), source.index('encode_skin_weights_inplace('))

    def test_chosen_mode_is_reported_and_written(self):
        source = _execute_source()
        report = 'ExportMode.mode_message(hammerspace_reasons)'
        write = "data['SluggiesModel']['UseHammerspace'] = use_hammerspace"
        self.assertIn(report, source)
        self.assertIn(write, source)
        self.assertLess(source.index(report), source.index(write))
        self.assertLess(source.index('ExportMode.promote_inplace_uv_edits('),
                        source.index('json.dump(data, f, indent=2)'))

    def test_in_place_losses_become_reasons(self):
        source = _execute_source()
        for marker in ('ExportMode.topology_changed(', 'ExportMode.colors_changed(',
                       'UV seams split', 'faces moved to other materials',
                       'normals split on shared normal slots',
                       'skin data no longer fits its original size',
                       'vertices moved between bones'):
            self.assertIn(marker, source)
        # Bone moves are checked before the in-place skin encoder runs.
        self.assertLess(source.index('skin_membership_changed(candidates, data)'),
                        source.index('encode_skin_weights_inplace('))
        self.assertNotIn('Did you remember to activate hammerspace mode', source)


if __name__ == '__main__':
    unittest.main()
