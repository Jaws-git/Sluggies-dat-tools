"""Variants: extra .sluggie files saved under new names next to (or away from) the exported one.

The Blender export can save a model under a new file name; the model export never deletes them; folder lookups
prefer the exported file (named after its folder) and pair High and Low models by name first.
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
ADDON_DIR = TOOLS_DIR.parent / 'BlenderAddonSrc'
for _path in (TOOLS_DIR, ADDON_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ModelCheckCore  # noqa: E402
import model_files  # noqa: E402
import model0  # noqa: E402
from Hammerspace import UntangledTextures  # noqa: E402
from Roster import gear  # noqa: E402


def _write(path, **model):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'SluggiesModel': model}, handle)
    return path


class VariantRuleTests(unittest.TestCase):
    """The rules exist twice (command line tools, Blender add-on): both copies must agree."""

    MODULES = (model_files, ModelCheckCore)

    def test_exported_name_is_the_folder_name(self):
        for module in self.MODULES:
            with self.subTest(module=module.__name__):
                self.assertTrue(module.is_exported_name(os.path.join('x', '1_mario.gpl', '1_mario.gpl.sluggie')))
                self.assertFalse(module.is_exported_name(os.path.join('x', '1_mario.gpl', '1_mario_red.gpl.sluggie')))

    def test_variant_key_drops_offset_and_low_marker(self):
        for module in self.MODULES:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.variant_key('78711424_L_mario_red.gpl.sluggie'), 'mario_red.gpl')
                self.assertEqual(module.variant_key('78277664_mario_red.gpl.sluggie'), 'mario_red.gpl')
                self.assertEqual(module.variant_key('Mario Red.sluggie'), 'mario red')

    def test_partner_by_name_then_exported(self):
        high = os.path.join('c', '1_mario.gpl')
        low = os.path.join('c', '2_L_mario.gpl')
        high_files = [os.path.join(high, '1_mario.gpl.sluggie'), os.path.join(high, '1_mario_red.gpl.sluggie')]
        for module in self.MODULES:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.pick_partner(high_files, os.path.join(low, '2_L_mario_red.gpl.sluggie')),
                                 high_files[1])
                self.assertEqual(module.pick_partner(high_files, os.path.join(low, '2_L_mario.gpl.sluggie')),
                                 high_files[0])
                # No Low variant of that name: the exported High file.
                self.assertEqual(module.pick_partner(high_files, os.path.join(low, 'blue.sluggie')), high_files[0])

    def test_partner_is_none_when_ambiguous(self):
        files = [os.path.join('c', 'h', 'a.sluggie'), os.path.join('c', 'h', 'b.sluggie')]
        for module in self.MODULES:
            with self.subTest(module=module.__name__):
                self.assertIsNone(module.pick_partner(files, os.path.join('c', 'l', 'L_c.sluggie')))
                self.assertEqual(module.pick_partner(files[:1], os.path.join('c', 'l', 'L_c.sluggie')), files[0])


class ExportTargetTests(unittest.TestCase):
    SOURCE = {'ChunkNumber': 18, 'FileIndex': 0, 'ModelOffset': '0x4AA6000'}

    def test_same_model_may_be_overwritten(self):
        target = dict(self.SOURCE, ModelOffset='0x4aa6000')
        self.assertIsNone(ModelCheckCore.overwrite_error(self.SOURCE, target, 'b.sluggie'))

    def test_other_model_is_refused(self):
        error = ModelCheckCore.overwrite_error(self.SOURCE, dict(self.SOURCE, FileIndex=1), 'b.sluggie')
        self.assertIn('different model', error)
        self.assertIn('file 1', error)

    def test_unreadable_target_is_refused(self):
        self.assertIn('not a readable', ModelCheckCore.overwrite_error(self.SOURCE, None, 'b.sluggie'))

    def test_clipboard_gets_a_bare_name_only_inside_the_models_folder(self):
        inside = os.path.join('E:' + os.sep, 'S', '2_Output_Assets', '18 Mario', 'm', 'm_red.sluggie')
        outside = os.path.join('E:' + os.sep, 'Mods', 'm_red.sluggie')
        self.assertEqual(ModelCheckCore.clipboard_text(inside), 'm_red.sluggie')
        self.assertEqual(ModelCheckCore.clipboard_text(outside), os.path.abspath(outside))


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())

    def test_gear_takes_the_exported_bat_over_a_variant(self):
        char = os.path.join(self.root, '22 Peach')
        model = _write(os.path.join(char, '1_peach.gpl', '1_peach.gpl.sluggie'), ChunkNumber=22, FileIndex=0)
        bat = _write(os.path.join(char, '100', '132_bat.gpl', '132_bat.gpl.sluggie'), ChunkNumber=22, FileIndex=2)
        _write(os.path.join(char, '100', '132_bat.gpl', '132_bat_gold.gpl.sluggie'), ChunkNumber=22, FileIndex=2)
        found, ambiguous = gear.find_gear(model, 22)
        self.assertEqual(found[2].path, bat)
        self.assertEqual(ambiguous, {})

    def test_untangle_baseline_reads_the_exported_file(self):
        folder = os.path.join(self.root, '89 Unused Yoshi A', '5_yoshi.gpl')
        _write(os.path.join(folder, '5_yoshi.gpl - a.sluggie'), ChunkNumber=89, FileIndex=0, Tag='variant')
        _write(os.path.join(folder, '5_yoshi.gpl.sluggie'), ChunkNumber=89, FileIndex=0, Tag='exported')
        self.assertEqual(UntangledTextures.find_sluggie_model(89, 0, self.root)['Tag'], 'exported')


class ModelExportKeepsFilesTests(unittest.TestCase):
    def test_variants_and_renamed_pngs_survive_a_re_export(self):
        with tempfile.TemporaryDirectory() as root:
            temp_dir = os.path.join(root, 'tex_temp')
            os.makedirs(temp_dir)
            with open(os.path.join(temp_dir, 'tex0_new.png'), 'wb') as handle:
                handle.write(b'new')
            out = os.path.join(root, '18 Mario') + os.sep
            folder = os.path.join(out, '1_mario.gpl')
            os.makedirs(os.path.join(folder, 'tex'))
            kept = [os.path.join(folder, name) for name in ('1_mario.gpl.sluggie', '1_mario_red.gpl.sluggie',
                                                            os.path.join('tex', 'red_body.png'))]
            for path in kept:
                with open(path, 'wb') as handle:
                    handle.write(b'old')

            model = object.__new__(model0.Model0)
            model.name, model.ACT, model.TEXPalette = '1_mario.gpl', None, None

            def model_data(**kwargs):
                model0._copy_texture_pngs(kwargs['texture_output_dir'])
                return SimpleNamespace(textures={})

            with mock.patch.object(model0, 'TEX_TEMP_DIR', temp_dir), \
                    mock.patch.object(model, 'model_data', side_effect=model_data, create=True):
                model.toFile(out, export_tex=True, export_glb=True)

            for path in kept:
                self.assertTrue(os.path.isfile(path), path)
            with open(os.path.join(folder, 'tex', 'tex0_new.png'), 'rb') as handle:
                self.assertEqual(handle.read(), b'new')


class ModelSizeTests(unittest.TestCase):
    def test_record_comes_from_the_file_not_its_name(self):
        from Hammerspace import ModelSize
        with tempfile.TemporaryDirectory() as root:
            path = _write(os.path.join(root, 'mario red.sluggie'), ChunkNumber=18, FileIndex=1)
            self.assertEqual(ModelSize.model_record(path, None), (18, 1))


class PatchOrderTests(unittest.TestCase):
    def test_low_file_known_by_its_file_index(self):
        sys.path.insert(0, str(TOOLS_DIR.parent))
        import start
        with tempfile.TemporaryDirectory() as root:
            low = _write(os.path.join(root, 'mario red low.sluggie'), FileIndex=1)
            high = _write(os.path.join(root, 'x_L_y high.sluggie'), FileIndex=0)
            self.assertEqual(sorted([low, high], key=start._is_low_file), [high, low])


if __name__ == '__main__':
    unittest.main()
