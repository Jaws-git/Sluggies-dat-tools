import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import maintenance
import model_files


def _write_sluggie(path, chunk, index, offset, **extra):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'SluggiesModel': {'ChunkNumber': chunk, 'FileIndex': index, 'ModelOffset': offset, **extra}},
                  handle, indent=2)


class DuplicateModelTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.models = os.path.join(self.root, maintenance.MODELS_FOLDER)

    def _path(self, *parts):
        return os.path.join(self.models, *parts)

    def test_clean_export_has_no_problems(self):
        _write_sluggie(self._path('18 Mario', 'a_mario.gpl', 'a_mario.gpl.sluggie'), 18, 0, '0x100')
        _write_sluggie(self._path('18 Mario', 'b_L_mario.gpl', 'b_L_mario.gpl.sluggie'), 18, 1, '0x200')
        self.assertEqual(maintenance.duplicate_models(self.root), [])

    def test_copied_model_folder_is_reported(self):
        original = self._path('18 Mario', 'a_mario.gpl', 'a_mario.gpl.sluggie')
        copy = self._path('Custom18 Mario 01', 'a_mario.gpl', 'a_mario.gpl.sluggie')
        _write_sluggie(original, 18, 0, '0x100')
        _write_sluggie(copy, 18, 0, '0x100')

        problems = maintenance.duplicate_models(self.root)

        self.assertEqual(len(problems), 1)
        self.assertEqual(set(problems[0].paths), {original, copy})
        self.assertIn('directory 18, file 0', problems[0].summary)

    def test_renamed_copy_in_the_same_folder_is_reported(self):
        # Explorer's "x - Copy.sluggie": a different name, the same model.
        folder = self._path('18 Mario', 'a_mario.gpl')
        _write_sluggie(os.path.join(folder, 'a_mario.gpl.sluggie'), 18, 0, '0x100')
        _write_sluggie(os.path.join(folder, 'a_mario.gpl - Copy.sluggie'), 18, 0, '0x100')
        self.assertEqual(len(maintenance.duplicate_models(self.root)), 1)

    def test_shared_block_exported_for_two_directories_is_not_reported(self):
        # The export writes a block shared by two directories into both folders, under one name.
        _write_sluggie(self._path('137 Various A', 'x_bomhei.gpl', 'x_bomhei.gpl.sluggie'), 137, 5, '0x26d0b260')
        _write_sluggie(self._path('142 Various B', 'x_bomhei.gpl', 'x_bomhei.gpl.sluggie'), 142, 9, '0x26d0b260')
        self.assertEqual(maintenance.duplicate_models(self.root), [])

    def test_slot_export_is_not_reported(self):
        # "Export as .sluggie" keeps the base model's identity on purpose (own name, own donor block).
        _write_sluggie(self._path('18 Mario', 'a_mario.gpl', 'a_mario.gpl.sluggie'), 18, 0, '0x100')
        export = self._path('Custom Mario 01', 'a_mario.gpl', 'a_mario_Mario01.gpl.sluggie')
        os.makedirs(os.path.dirname(export))
        with open(export, 'w', encoding='utf-8') as handle:
            json.dump({'SluggiesModel': {'ChunkNumber': 18, 'FileIndex': 0, 'ModelOffset': '0x100',
                                         'ModelLength': 64, 'SlotExport': {'Character': '0x00'}}}, handle, indent=2)
        self.assertTrue(maintenance.is_slot_export(export))
        self.assertEqual(maintenance.duplicate_models(self.root), [])

    def test_archive_members_are_not_reported(self):
        # Container members share directory and file; their offsets tell them apart.
        _write_sluggie(self._path('136 Items', '1', 'a_ball.gpl', 'a_ball.gpl.sluggie'), 136, 0, '0x100')
        _write_sluggie(self._path('136 Items', '1', 'b_shadow.gpl', 'b_shadow.gpl.sluggie'), 136, 0, '0x900')
        self.assertEqual(maintenance.duplicate_models(self.root), [])

    def test_identity_found_beyond_the_head(self):
        # A writer that puts other keys first: the full parse still finds the model.
        path = self._path('18 Mario', 'a.sluggie')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({'SluggiesModel': {'Padding': 'x' * 10000, 'ChunkNumber': 18, 'FileIndex': 0,
                                         'ModelOffset': '0x100'}}, handle)
        self.assertEqual(maintenance.model_identity(path), (18, 0, '0x100'))

    def test_unreadable_copy_is_reported_by_name(self):
        good = self._path('18 Mario', 'a.sluggie')
        broken = self._path('18 Mario copy', 'a.sluggie')
        _write_sluggie(good, 18, 0, '0x100')
        os.makedirs(os.path.dirname(broken))
        with open(broken, 'w', encoding='utf-8') as handle:
            handle.write('{ not json')

        problems = maintenance.duplicate_models(self.root)

        self.assertEqual(len(problems), 1)
        self.assertEqual(set(problems[0].paths), {good, broken})

    def test_missing_models_folder_has_no_problems(self):
        self.assertEqual(maintenance.scan(os.path.join(self.root, 'nowhere')), ([], []))


class ScanTests(unittest.TestCase):
    def test_a_failing_check_does_not_hide_the_others(self):
        def broken(_root):
            raise RuntimeError('boom')
        found = maintenance.Problem('Other', 'something', ('x',), 'fix it')
        checks = (('Broken', broken), ('Other', lambda _root: [found]))
        with mock.patch.object(maintenance, 'CHECKS', checks):
            problems, errors = maintenance.scan('unused')
        self.assertEqual(problems, [found])
        self.assertEqual(errors, [('Broken', 'RuntimeError: boom')])


class FindUniqueTests(unittest.TestCase):
    def test_none_one_and_several(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(model_files.find_unique(root, 'a.sluggie'))
            first = os.path.join(root, 'one', 'a.sluggie')
            os.makedirs(os.path.dirname(first))
            open(first, 'w').close()
            self.assertEqual(model_files.find_unique(root, 'a.sluggie'), first)
            second = os.path.join(root, 'two', 'a.sluggie')
            os.makedirs(os.path.dirname(second))
            open(second, 'w').close()
            with self.assertRaises(model_files.AmbiguousNameError) as caught:
                model_files.find_unique(root, 'a.sluggie')
            self.assertEqual(caught.exception.matches, [first, second])


if __name__ == '__main__':
    unittest.main()
