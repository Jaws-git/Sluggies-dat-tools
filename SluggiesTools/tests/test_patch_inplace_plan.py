"""``InplacePatcher/patch_inplace.py`` called in-process: the command line, ``plan`` and ``apply`` on a
hand-built DAT (``test_patch_inplace_run.py`` runs the same script as the child process it is)."""

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
for _path in (TOOLS_DIR, TOOLS_DIR / 'InplacePatcher', TOOLS_DIR / 'tests'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import patch_inplace  # noqa: E402
from binfmt import dump_sluggie, encode_field  # noqa: E402
from test_patch_inplace_run import BLOCK, BLOCK_LENGTH, NEIGHBOUR, NEW, OLD, VERTS, _dat, _model  # noqa: E402


class ParseArgsTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = str(self.root / 'model.sluggie')
        dump_sluggie(_model(), self.path)

    def test_flags(self):
        self.assertEqual(patch_inplace.parse_args([self.path]), patch_inplace.Options(self.path))
        options = patch_inplace.parse_args(['--unpatch', self.path])
        self.assertTrue(options.unpatch)
        options = patch_inplace.parse_args([self.path, '--validate-only', '--texture-file', 'a.png',
                                            '--texture-index', '3'])
        self.assertEqual((options.validate_only, options.texture_file, options.texture_index), (True, 'a.png', 3))

    def test_usage_errors_are_refused(self):
        for argv, text in (
            ([], 'No .sluggies file path'),
            ([str(self.root / 'none.sluggie')], 'JSON file not found'),
            ([self.path, '--unpatch', '--validate-only'], 'does not combine'),
            ([self.path, '--unpatch', '--texture-file', 'a.png'], 'does not accept .png'),
            ([self.path, '--texture-file'], 'requires a PNG path'),
            ([self.path, '--texture-index', 'x'], 'must be an integer'),
        ):
            with self.subTest(argv=argv[1:]), self.assertRaisesRegex(patch_inplace.PatchRefused, text):
                patch_inplace.parse_args(argv)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.input = self.root / 'input.dat'
        self.input.write_bytes(_dat())
        self.output = self.root / 'out' / 'dt_na.dat'
        self.path = str(self.root / 'model.sluggie')
        for name, value in (('INPUT_DAT', str(self.input)), ('OUTPUT_DAT', str(self.output))):
            self.enterContext(mock.patch.object(patch_inplace, name, value))
        hh = patch_inplace._hh
        self.enterContext(mock.patch.object(hh, 'INPUT_DOL', str(self.root / 'no_main.dol')))
        self.enterContext(mock.patch.object(patch_inplace.DatAllocation, 'outputDatProblem', return_value=None))
        self.enterContext(mock.patch.object(
            patch_inplace.DatAllocation, 'copyFileAtomic', side_effect=lambda source, target: (
                pathlib.Path(target).parent.mkdir(parents=True, exist_ok=True),
                pathlib.Path(target).write_bytes(pathlib.Path(source).read_bytes()))))
        self.ask = self.enterContext(mock.patch.object(patch_inplace._slogger, 'ask', return_value=''))

    def _plan(self, document, **options):
        return patch_inplace.plan(document, patch_inplace.Options(self.path, **options))

    def test_vertex_edit_is_planned_and_nothing_is_written(self):
        plan = self._plan(_model())
        self.assertEqual((plan.block_start, plan.block_length), (BLOCK, BLOCK_LENGTH))
        self.assertEqual(plan.writes, [('Submesh 0 vertex', VERTS, NEW)])
        self.assertEqual(plan.counts['vertex'], 1)
        self.assertEqual(sum(plan.counts.values()), 1)
        self.assertEqual((plan.texture_writes, plan.skin_data, plan.skin_resized), ((), None, False))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.input.read_bytes(), _dat())

    def test_unpatch_plans_the_original_bytes(self):
        plan = self._plan(_model(), unpatch=True)
        self.assertEqual(plan.writes, [('Submesh 0 vertex', VERTS, OLD)])

    def test_resized_buffer_refuses_the_whole_patch(self):
        document = _model(VertexBufferDataEdited=encode_field(NEW + NEW, False))
        with self.assertRaisesRegex(patch_inplace.PatchRefused, 'In-place patch refused, nothing written: .*submesh 0'):
            self._plan(document)
        with self.assertRaisesRegex(patch_inplace.PatchRefused, 'In-place build check failed: .*submesh 0'):
            self._plan(document, validate_only=True)

    def test_write_outside_the_block_is_refused(self):
        document = _model(VertexBufferOffset=hex(NEIGHBOUR + 0x10))
        with self.assertRaisesRegex(patch_inplace.PatchRefused, "outside the model's block"):
            self._plan(document)

    def test_shader_mode_edits(self):
        document = _model()
        state = {'DisplayStateId': 7, 'ShaderMode': 'Spec', 'ShaderModeEdited': 'RhSp',
                 'ShaderModeFieldOffset': hex(BLOCK + 0x60)}
        document['SluggiesModel']['Submeshes'][0]['DisplayStates'] = [state]
        self.assertIn(('Submesh 0 DS[0] shader', BLOCK + 0x60, b'RhSp'), self._plan(document).writes)
        self.assertIn(('Submesh 0 DS[0] shader', BLOCK + 0x60, b'Spec'), self._plan(document, unpatch=True).writes)
        state['ShaderModeEdited'] = 'Shdw'
        with self.assertRaisesRegex(patch_inplace.PatchRefused, 'lighting / no-lighting boundary'):
            self._plan(document)
        state['ShaderModeEdited'] = 'Nope'
        with self.assertRaisesRegex(patch_inplace.PatchRefused, 'unrecognised shader mode'):
            self._plan(document)

    def test_hammerspace_only_edits_are_refused(self):
        document = _model()
        document['SluggiesModel']['Submeshes'][0]['RigidRebuild'] = {'Reason': 'x'}
        with self.assertRaisesRegex(patch_inplace.PatchRefused, 'Rigid rebuilds require Hammerspace'):
            self._plan(document)
        self._plan(document, unpatch=True)              # restoring the original bytes is always possible

    def test_apply_writes_the_plan(self):
        patch_inplace.prepare_output(patch_inplace.Options(self.path))
        self.assertEqual(self.output.read_bytes(), _dat())
        patch_inplace.apply(self._plan(_model()))
        expected = bytearray(_dat())
        expected[VERTS:VERTS + len(NEW)] = NEW
        self.assertEqual(self.output.read_bytes(), bytes(expected))

    def test_main_patches_and_reports_a_refusal(self):
        dump_sluggie(_model(), self.path)
        self.assertEqual(patch_inplace.main([self.path]), 0)
        self.assertEqual(self.output.read_bytes()[VERTS:VERTS + len(NEW)], NEW)
        self.assertEqual(patch_inplace.main([self.path, '--unpatch']), 0)
        self.assertEqual(self.output.read_bytes(), _dat())
        self.ask.assert_not_called()

        dump_sluggie(_model(VertexBufferDataEdited=encode_field(NEW + NEW, False)), self.path)
        self.assertEqual(patch_inplace.main([self.path, '--validate-only']), 1)
        self.ask.assert_not_called()                    # a build check never prompts
        self.assertEqual(patch_inplace.main([self.path]), 1)
        self.ask.assert_called_once()
        self.assertEqual(self.output.read_bytes(), _dat())

    def test_build_check_writes_nothing(self):
        dump_sluggie(_model(), self.path)
        self.assertEqual(patch_inplace.main([self.path, '--validate-only']), 0)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
