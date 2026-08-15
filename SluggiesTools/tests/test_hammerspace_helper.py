import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
if str(HAMMERSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(HAMMERSPACE_DIR))

import HammerspaceHelper as helper


class FindFreeMemoryChunkTests(unittest.TestCase):
    def _find(self, region, length, *, base_size=64, chunk_size=64, alignment=32):
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_path = pathlib.Path(temp_dir) / 'dt_na.dat'
            dat_path.write_bytes(b'X' * base_size + region)
            with (
                mock.patch.object(helper, 'OUTPUT_DAT', str(dat_path)),
                mock.patch.object(helper, 'BASE_SIZE', base_size),
                mock.patch.object(helper, 'CHUNK_SIZE', chunk_size),
                mock.patch.object(helper, 'HS_ALIGN_BYTES', alignment),
            ):
                return helper.findFreeMemoryChunk(length)

    def test_checks_every_byte_inside_aligned_block(self):
        region = bytearray(96)
        region[17] = 1

        self.assertEqual(self._find(bytes(region), 32), 96)

    def test_checks_partial_tail_after_full_blocks(self):
        region = bytearray(128)
        region[34] = 1

        self.assertEqual(self._find(bytes(region), 35), 128)

    def test_zero_run_can_cross_read_chunk_boundary(self):
        region = b'X' * 32 + b'\x00' * 96

        self.assertEqual(self._find(region, 96), 96)

    def test_scan_begins_at_first_aligned_hammerspace_offset(self):
        self.assertEqual(
            self._find(b'\x00' * 29, 8, base_size=3, chunk_size=16, alignment=8),
            8,
        )


class RemoveModelFromHammerspaceTests(unittest.TestCase):
    def _files(self, temp_dir, *, current_offset):
        root = pathlib.Path(temp_dir)
        input_dat = root / 'input.dat'
        output_dat = root / 'output.dat'
        output_dol = root / 'output.dol'
        original = b'ORIGINAL'
        original_offset = 8
        hammerspace_offset = 64
        input_dat.write_bytes(b'I' * original_offset + original + b'I' * 64)
        output = bytearray(96)
        output[original_offset:original_offset + len(original)] = b'\x00' * len(original)
        output[hammerspace_offset:hammerspace_offset + len(original)] = b'PATCHED!'
        output_dat.write_bytes(output)
        dol = bytearray(48)
        struct.pack_into('>II', dol, 4, len(original), current_offset)
        output_dol.write_bytes(dol)
        return input_dat, output_dat, output_dol, original_offset, hammerspace_offset, original

    def test_restores_original_bytes_before_redirecting_and_zeros_hammerspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._files(temp_dir, current_offset=64)
            input_dat, output_dat, output_dol, original_offset, hs_offset, original = paths

            def assert_restored_before_dol_patch(*_args):
                data = output_dat.read_bytes()
                self.assertEqual(data[original_offset:original_offset + len(original)], original)
                self.assertEqual(data[hs_offset:hs_offset + len(original)], b'PATCHED!')

            with (
                mock.patch.object(helper, 'INPUT_DAT', str(input_dat)),
                mock.patch.object(helper, 'OUTPUT_DAT', str(output_dat)),
                mock.patch.object(helper, 'OUTPUT_DOL', str(output_dol)),
                mock.patch.object(helper, 'BASE_SIZE', 64),
                mock.patch.object(helper, '_readDirPtrs', return_value=[0]),
                mock.patch.object(helper, 'readDolEntry', return_value=(original_offset, len(original))),
                mock.patch.object(helper, 'findSharedEntries', return_value=[]),
                mock.patch.object(helper, 'patchDolEntry', side_effect=assert_restored_before_dol_patch),
            ):
                success, removed_offset, removed_length = helper.removeModelFromHammerspace(0, 0)

            data = output_dat.read_bytes()
            self.assertTrue(success)
            self.assertEqual((removed_offset, removed_length), (hs_offset, len(original)))
            self.assertEqual(data[original_offset:original_offset + len(original)], original)
            self.assertEqual(data[hs_offset:hs_offset + len(original)], b'\x00' * len(original))

    def test_repairs_original_bytes_when_dol_was_already_restored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._files(temp_dir, current_offset=8)
            input_dat, output_dat, output_dol, original_offset, _, original = paths
            with (
                mock.patch.object(helper, 'INPUT_DAT', str(input_dat)),
                mock.patch.object(helper, 'OUTPUT_DAT', str(output_dat)),
                mock.patch.object(helper, 'OUTPUT_DOL', str(output_dol)),
                mock.patch.object(helper, 'BASE_SIZE', 64),
                mock.patch.object(helper, '_readDirPtrs', return_value=[0]),
                mock.patch.object(helper, 'readDolEntry', return_value=(original_offset, len(original))),
            ):
                result = helper.removeModelFromHammerspace(0, 0)

            data = output_dat.read_bytes()
            self.assertEqual(result, (True, 0, 0))
            self.assertEqual(data[original_offset:original_offset + len(original)], original)


if __name__ == '__main__':
    unittest.main()