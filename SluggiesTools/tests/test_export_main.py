"""The export run (``export.py``) after its split: importing it does nothing, the names that moved to
``sluggie_document.py`` and ``dol_dirs.py`` stay importable from it, ``main(argv)`` returns the exit code, and the
``--untangle`` preparation reads and writes the output DAT through one handle, shows a broken
``HammerspaceHelper`` as the import error it is, and lets a failed DOL record end the run.

Everything runs on temp files; the logger is replaced, so nothing reaches ``3_Output_Dat``."""
import io
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import dol_dirs  # noqa: E402
import export  # noqa: E402
import sluggie_document  # noqa: E402


class _QuietLogger(unittest.TestCase):
    """Replaces the export's logger: no log file, no prompt."""

    def setUp(self):
        patcher = mock.patch.object(export, '_slogger')
        self.slogger = patcher.start()
        self.addCleanup(patcher.stop)


class ImportTests(unittest.TestCase):
    def test_importing_reads_no_flags_and_sets_nothing_up(self):
        code = ("import sys; sys.path.insert(0, sys.argv.pop()); sys.argv = ['export.py', '--debug', '--untangle']; "
                "import export, slogger, sluggie_document; "
                "print('HammerspaceHelper' in sys.modules, slogger._initialized, sluggie_document._use_compression)")
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, '-c', code, str(TOOLS_DIR)], cwd=cwd,
                                    capture_output=True, text=True)
            self.assertEqual(result.stdout.split(), ['False', 'False', 'True'], result.stderr)
            self.assertEqual(os.listdir(cwd), [])

    def test_moved_names_stay_importable_from_export(self):
        self.assertEqual([name for name in export.__all__ if not hasattr(export, name)], [])
        for name in ('model_document', 'write_model_document', 'compact_faces_json', 'extract_bone_data',
                     'extract_submeshes', 'extract_texture_descriptors'):
            self.assertIs(getattr(export, name), getattr(sluggie_document, name), name)
        for name in ('load_dol_dirs', 'folderNameMap', 'top_level_folder_name', 'DIRS_START', 'DAT_FNAME_PTR'):
            self.assertIs(getattr(export, name), getattr(dol_dirs, name), name)
        self.assertEqual(export.top_level_folder_name(18), '18 Mario')
        self.assertEqual(export.top_level_folder_name(3), '3')

    def test_compression_is_a_setting_of_the_document_module(self):
        self.addCleanup(sluggie_document.set_compression, True)
        self.assertEqual(sluggie_document._encode_bytes(b'\x01\xff'), 'x:01ff')
        sluggie_document.set_compression(False)
        self.assertEqual(sluggie_document._encode_bytes(b'\x01\xff'), [1, 255])


class LoadDolDirsTests(unittest.TestCase):
    def test_a_directory_ends_at_the_first_non_record(self):
        records_at, empty_at = 0x1000, 0x2000
        dol = bytearray(dol_dirs.DIRS_END)
        pointers = [empty_at + 0x80003f00] * dol_dirs.DIRS_LEN
        pointers[18] = records_at + 0x80003f00
        struct.pack_into(f'>{dol_dirs.DIRS_LEN}I', dol, dol_dirs.DIRS_START, *pointers)
        for index, (offset, length) in enumerate(((0x40, 0x10), (0x80, 0x20))):
            struct.pack_into('>12I', dol, records_at + 48 * index,
                             *([dol_dirs.DAT_FNAME_PTR, length, offset, length] * 3))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'main.dol')
            with open(path, 'wb') as handle:
                handle.write(dol)
            dirs = dol_dirs.load_dol_dirs(path)
        self.assertEqual(len(dirs), dol_dirs.DIRS_LEN)
        self.assertEqual(dirs[18], [{'en': [0x40, 0x10], 'sp': [0x40, 0x10], 'fr': [0x40, 0x10]},
                                    {'en': [0x80, 0x20], 'sp': [0x80, 0x20], 'fr': [0x80, 0x20]}])
        self.assertEqual(dirs[19], [])


class MainTests(_QuietLogger):
    def test_a_declined_overwrite_ends_with_exit_code_0(self):
        with mock.patch.object(export, 'confirm_overwrite', return_value=False), \
                mock.patch.object(export, 'load_dol_dirs') as load:
            self.assertEqual(export.main(['--notex']), 0)
        load.assert_not_called()
        self.slogger.clear_log_file.assert_not_called()

    def test_missing_wimgt_ends_with_exit_code_1_before_anything_is_read(self):
        with mock.patch.object(export, 'confirm_overwrite', return_value=True), \
                mock.patch.object(export.shutil, 'which', return_value=None), \
                mock.patch.object(export, 'load_dol_dirs') as load:
            self.assertEqual(export.main([]), 1)
        load.assert_not_called()

    def test_flags_come_from_argv(self):
        self.addCleanup(sluggie_document.set_compression, True)
        seen = {}

        def directories(dat, dirs, untangle_context=None, **flags):
            seen.update(flags, context=untangle_context, compressed=sluggie_document._use_compression)
            return [(18, 0, 'ValueError: broken')]

        with tempfile.TemporaryDirectory() as tmp:
            dat_path = os.path.join(tmp, 'dt_na.dat')
            with open(dat_path, 'wb') as handle:
                handle.write(bytes(16))
            real_open = open
            with mock.patch.object(export, 'confirm_overwrite', return_value=True), \
                    mock.patch.object(export, 'load_dol_dirs', return_value={}), \
                    mock.patch.object(export, 'export_directories', side_effect=directories), \
                    mock.patch.object(export, 'write_texture_hash_overlaps_report'), \
                    mock.patch.object(export, 'open', create=True,
                                      side_effect=lambda path, mode: real_open(dat_path, mode)):
                # --untangle with --notex is switched off with a warning: no output file is prepared.
                code = export.main(['--notex', '--debug', '--glb', '--anim-glb', '--untangle'])
        self.assertEqual(code, 1)                          # a model that could not be written
        self.assertEqual(seen, {'export_tex': False, 'export_glb': True, 'export_anim': True,
                                'untangle_tex': True, 'context': None, 'compressed': False})

    def test_entries_that_are_no_models_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            dat = export.Dat(io.BytesIO(struct.pack('>4I', 0x12345678, 0, 0, 0)))
            with mock.patch.object(export, 'outdir', tmp + '/'):
                failed = export.export_directories(dat, {18: [{'en': [0, 16]}], 89: [{'en': [0, 16]}]},
                                                   export_tex=False)
            self.assertEqual(failed, [])
            self.assertEqual(os.listdir(tmp), [])              # the empty "18 Mario" folder is removed again
            self.assertEqual(dat.children, [])


class UntanglePrepareTests(_QuietLogger):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.input_dir = os.path.join(tmp.name, '1_Input')
        self.output_dir = os.path.join(tmp.name, '3_Output_Dat')
        os.mkdir(self.input_dir)
        for name, data in (('dt_na.dat', b'input dat'), ('main.dol', b'input dol')):
            with open(os.path.join(self.input_dir, name), 'wb') as handle:
                handle.write(data)

    def _prepare(self):
        return export.prepare_untangle_output_files(self.input_dir, self.output_dir)

    def _output(self, name):
        with open(os.path.join(self.output_dir, name), 'rb') as handle:
            return handle.read()

    def test_both_files_are_copied_and_no_temporary_file_stays(self):
        dat_path, dol_path = self._prepare()
        self.assertEqual((os.path.basename(dat_path), os.path.basename(dol_path)), ('dt_na.dat', 'main.dol'))
        self.assertEqual((self._output('dt_na.dat'), self._output('main.dol')), (b'input dat', b'input dol'))
        self.assertEqual(sorted(os.listdir(self.output_dir)), ['dt_na.dat', 'main.dol'])
        self.slogger.ask.assert_not_called()

    def test_declining_keeps_the_existing_output(self):
        os.mkdir(self.output_dir)
        with open(os.path.join(self.output_dir, 'dt_na.dat'), 'wb') as handle:
            handle.write(b'patched')
        self.slogger.ask.return_value = ' N '
        self.assertEqual(self._prepare(), (None, None))
        self.assertEqual(self._output('dt_na.dat'), b'patched')
        self.assertEqual(os.listdir(self.output_dir), ['dt_na.dat'])

    def test_an_interrupted_copy_leaves_the_old_output_whole(self):
        os.mkdir(self.output_dir)
        with open(os.path.join(self.output_dir, 'dt_na.dat'), 'wb') as handle:
            handle.write(b'patched')
        self.slogger.ask.return_value = 'y'

        def cut_off(source, target):
            with open(target, 'wb') as handle:
                handle.write(b'inp')
            raise KeyboardInterrupt

        with mock.patch.object(export.shutil, 'copyfile', side_effect=cut_off):
            with self.assertRaises(KeyboardInterrupt):
                self._prepare()
        self.assertEqual(self._output('dt_na.dat'), b'patched')
        self.assertEqual(os.listdir(self.output_dir), ['dt_na.dat'])


class OneDatHandleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, 'dt_na.dat')
        with open(self.path, 'wb') as handle:
            handle.write(bytes(range(64)))

    def test_untangle_writes_and_reads_go_through_the_same_handle(self):
        context = export.UntangleContext()
        with export.open_export_dat(self.path, context) as dat:
            handle = context.dat_output_handle
            self.assertIs(handle, dat.f)
            self.assertEqual(handle.mode, 'rb+')
            self.assertEqual(dat.read_at(0x10, 4), bytes((0x10, 0x11, 0x12, 0x13)))   # fills the read buffer
            handle.seek(0x10)
            handle.write(b'\xaa\xbb')
            self.assertEqual(dat.read_at(0x10, 4), b'\xaa\xbb\x12\x13')               # no stale second buffer
            self.assertEqual(dat.length, 64)
        self.assertTrue(handle.closed)
        with open(self.path, 'rb') as check:
            self.assertEqual(check.read()[0x0F:0x13], b'\x0f\xaa\xbb\x12')

    def test_the_handle_is_closed_and_flushed_when_the_export_raises(self):
        context = export.UntangleContext()
        with self.assertRaisesRegex(RuntimeError, 'model'):
            with export.open_export_dat(self.path, context):
                context.dat_output_handle.seek(4)
                context.dat_output_handle.write(b'\xee')
                raise RuntimeError('a model failed badly')
        self.assertTrue(context.dat_output_handle.closed)
        with open(self.path, 'rb') as check:
            self.assertEqual(check.read(6), b'\x00\x01\x02\x03\xee\x05')

    def test_a_plain_export_opens_the_input_read_only(self):
        with export.open_export_dat(self.path) as dat:
            self.assertEqual(dat.f.mode, 'rb')
            with self.assertRaises(io.UnsupportedOperation):
                dat.f.write(b'\x00')
        self.assertTrue(dat.f.closed)


class HammerspaceHelperTests(_QuietLogger):
    def test_a_broken_helper_shows_its_import_error(self):
        with mock.patch.dict(sys.modules, {'HammerspaceHelper': None}):
            with self.assertRaises(ImportError):
                export.clone_unused_dirs_to_hammerspace_for_untangle('main.dol', 'dt_na.dat')

    def test_the_import_error_comes_before_the_output_files_are_replaced(self):
        with mock.patch.dict(sys.modules, {'HammerspaceHelper': None}), \
                mock.patch.object(export, 'confirm_overwrite', return_value=True), \
                mock.patch.object(export, 'prepare_untangle_output_files') as prepare:
            with self.assertRaises(ImportError):
                export.main(['--untangle'])
        prepare.assert_not_called()
        self.slogger.clear_log_file.assert_not_called()

    def test_a_record_that_cannot_be_repointed_ends_the_clone(self):
        """``patchDolEntry`` raises; the clone step does not go on to the next record."""
        written = []

        def patch_entry(chunk, file_index, offset, length):
            raise RuntimeError(f'chunk={chunk}, file_index={file_index} is not a directory record')

        with tempfile.TemporaryDirectory() as tmp:
            dat_path = os.path.join(tmp, 'dt_na.dat')
            with open(dat_path, 'wb') as handle:
                handle.write(bytes(range(32)))
            helper = SimpleNamespace(
                HS_BUFFER_BYTES=128, HS_ALIGN_BYTES=32, BASE_SIZE=32, OUTPUT_DAT=dat_path,
                ensureOutputDat=lambda size: True, patchFstFileSize=lambda size: False,
                routedHammerspaceRanges=lambda: [],
                findFreeMemoryChunk=lambda length, reserved_ranges=(): 0x101,
                writeModelBlock=lambda block, offset: written.append((offset, block)),
                patchDolEntry=patch_entry)
            dirs = {89: [{'en': [4, 8]}, {'en': [0, 0]}, {'en': [16, 4]}], 18: [{'en': [0, 4]}]}
            with mock.patch.object(export, '_hammerspace_modules', return_value=(helper, helper, helper)), \
                    mock.patch.object(export, 'load_dol_dirs', return_value=dirs):
                with self.assertRaisesRegex(RuntimeError, 'chunk=89, file_index=0'):
                    export.clone_unused_dirs_to_hammerspace_for_untangle('main.dol', dat_path)
                self.assertEqual(written, [(0x120, bytes(range(4, 12)))])     # aligned to 32, then it stopped

                helper.patchDolEntry = lambda chunk, file_index, offset, length: None
                del written[:]
                report = []
                self.assertEqual(export.clone_unused_dirs_to_hammerspace_for_untangle('main.dol', dat_path, report), 2)
                self.assertEqual(written, [(0x120, bytes(range(4, 12))), (0x1C0, bytes(range(16, 20)))])
                self.assertEqual(len(report), 2)


if __name__ == '__main__':
    unittest.main()
