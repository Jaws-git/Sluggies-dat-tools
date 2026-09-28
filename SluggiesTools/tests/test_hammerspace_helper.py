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
    def _find(self, region, length, *, base_size=64, chunk_size=64, alignment=32, reserved=()):
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_path = pathlib.Path(temp_dir) / 'dt_na.dat'
            dat_path.write_bytes(b'X' * base_size + region)
            with (
                mock.patch.object(helper, 'OUTPUT_DAT', str(dat_path)),
                mock.patch.object(helper, 'BASE_SIZE', base_size),
                mock.patch.object(helper, 'CHUNK_SIZE', chunk_size),
                mock.patch.object(helper, 'HS_ALIGN_BYTES', alignment),
            ):
                return helper.findFreeMemoryChunk(length, reserved_ranges=reserved)

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


    def test_zero_tail_of_a_reserved_live_block_is_not_free(self):
        # A live 64-byte block whose last 32 bytes are zero padding, then free space.
        region = b'D' * 32 + b'\x00' * 32 + b'\x00' * 64

        self.assertEqual(self._find(region, 64), 96)
        self.assertEqual(self._find(region, 64, reserved=[(64, 64)]), 128)

    def test_partial_tail_respects_reserved_ranges(self):
        region = b'\x00' * 128

        self.assertEqual(self._find(region, 35, reserved=[(100, 4)]), 64)
        self.assertEqual(self._find(region, 35, reserved=[(98, 4)]), 128)

    def test_overlapping_reserved_ranges_are_merged(self):
        self.assertEqual(
            helper._normalize_reserved_ranges([(10, 5), (0, 0), (12, 10), (40, 2)]),
            [(10, 22), (40, 42)],
        )


class RoutedHammerspaceRangesTests(unittest.TestCase):
    def test_collects_hammerspace_ranges_from_every_language_slot(self):
        def entry(slots):
            words = []
            for length, offset in slots:
                words += [helper._DAT_FNAME_PTR, length, offset, length]
            return struct.pack('>12I', *words)

        dol = (
            entry([(0x40, 0x2000), (0x40, 0x2000), (0x80, 0x3000)])
            + entry([(0x10, 0x0500), (0x10, 0x0500), (0x10, 0x0500)])
            + b'\x00' * 48
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            dol_path = pathlib.Path(temp_dir) / 'main.dol'
            dol_path.write_bytes(dol)
            with (
                mock.patch.object(helper, 'OUTPUT_DOL', str(dol_path)),
                mock.patch.object(helper, 'BASE_SIZE', 0x1000),
                mock.patch.object(helper, '_readDirPtrs', return_value=[0]),
            ):
                self.assertEqual(helper.routedHammerspaceRanges(), [(0x2000, 0x40), (0x3000, 0x80)])

    def test_missing_output_dol_returns_no_ranges(self):
        with mock.patch.object(helper, 'OUTPUT_DOL', '/nonexistent/main.dol'):
            self.assertEqual(helper.routedHammerspaceRanges(), [])


class WriteModelBlockTests(unittest.TestCase):
    def test_writes_and_verifies_model_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_path = pathlib.Path(temp_dir) / 'dt_na.dat'
            dat_path.write_bytes(b'\x00' * 256)
            with (
                mock.patch.object(helper, 'OUTPUT_DAT', str(dat_path)),
                mock.patch.object(helper, 'HS_BUFFER_BYTES', 0),
            ):
                helper.writeModelBlock(b'model-block', 64)

            self.assertEqual(dat_path.read_bytes()[64:75], b'model-block')

    def test_detects_failed_write_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_path = pathlib.Path(temp_dir) / 'dt_na.dat'
            dat_path.write_bytes(b'\x00' * 256)
            real_open = open

            class CorruptingReader:
                def __init__(self, wrapped):
                    self.wrapped = wrapped

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.wrapped.close()

                def seek(self, *args):
                    return self.wrapped.seek(*args)

                def read(self, size):
                    return b'X' * size

            def open_with_corrupt_read(path, mode='r', *args, **kwargs):
                wrapped = real_open(path, mode, *args, **kwargs)
                if mode == 'rb':
                    return CorruptingReader(wrapped)
                return wrapped

            with (
                mock.patch.object(helper, 'OUTPUT_DAT', str(dat_path)),
                mock.patch.object(helper, 'HS_BUFFER_BYTES', 0),
                mock.patch('builtins.open', side_effect=open_with_corrupt_read),
            ):
                with self.assertRaisesRegex(IOError, 'verification failed'):
                    helper.writeModelBlock(b'model-block', 64)


class FindSharedEntriesTests(unittest.TestCase):
    def _write_dol(self, path, offsets):
        data = bytearray(192)
        for entry_offset, dat_offset in zip((0, 96), offsets):
            words = [helper._DAT_FNAME_PTR, 8, dat_offset, 8] * 3
            struct.pack_into('>12I', data, entry_offset, *words)
        path.write_bytes(data)

    def test_untangled_output_entries_are_not_treated_as_shared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            input_dol = root / 'input.dol'
            output_dol = root / 'output.dol'
            self._write_dol(input_dol, (8, 8))
            self._write_dol(output_dol, (64, 96))

            with (
                mock.patch.object(helper, 'INPUT_DOL', str(input_dol)),
                mock.patch.object(helper, 'OUTPUT_DOL', str(output_dol)),
                mock.patch.object(helper, '_readDirPtrs', return_value=[0, 96]),
            ):
                self.assertEqual(helper.findSharedEntries(0, 0), [])

    def test_currently_shared_output_entries_are_returned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            input_dol = root / 'input.dol'
            output_dol = root / 'output.dol'
            self._write_dol(input_dol, (8, 8))
            self._write_dol(output_dol, (64, 64))

            with (
                mock.patch.object(helper, 'INPUT_DOL', str(input_dol)),
                mock.patch.object(helper, 'OUTPUT_DOL', str(output_dol)),
                mock.patch.object(helper, '_readDirPtrs', return_value=[0, 96]),
            ):
                self.assertEqual(helper.findSharedEntries(0, 0), [(1, 0)])

    def _records(self, path, dat_offsets):
        """Write contiguous records (no gaps) with the given en offsets."""
        data = bytearray(48 * (len(dat_offsets) + 1))
        for index, dat_offset in enumerate(dat_offsets):
            struct.pack_into('>12I', data, index * 48, *([helper._DAT_FNAME_PTR, 8, dat_offset, 8] * 3))
        path.write_bytes(data)

    def test_directory_walk_stops_at_the_next_directory(self):
        # Dirs 0/1/2 start at records 0/2/3; record 2 is (1,0) and its offset is
        # unique. The old unbounded walk also reported it as (0,2), an alias.
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dol = pathlib.Path(temp_dir) / 'output.dol'
            self._records(output_dol, (0x10, 0x20, 0x30, 0x40))
            with (
                mock.patch.object(helper, 'OUTPUT_DOL', str(output_dol)),
                mock.patch.object(helper, '_readDirPtrs', return_value=[0, 96, 144]),
            ):
                self.assertEqual(helper.findSharedEntries(1, 0), [])

    def test_genuine_sharer_in_another_directory_is_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dol = pathlib.Path(temp_dir) / 'output.dol'
            self._records(output_dol, (0x10, 0x20, 0x30, 0x30))
            with (
                mock.patch.object(helper, 'OUTPUT_DOL', str(output_dol)),
                mock.patch.object(helper, '_readDirPtrs', return_value=[0, 96, 144]),
            ):
                self.assertEqual(helper.findSharedEntries(1, 0), [(2, 0)])
                self.assertEqual(helper.findSharedEntries(2, 0), [(1, 0)])


class ZeroOriginalModelTests(unittest.TestCase):
    """Two directories share one vanilla block, like an unused character and its owner."""

    ORIGINAL_OFFSET = 8
    ORIGINAL = b'ORIGINAL'

    def _run(self, output_offsets):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            output_dat = root / 'output.dat'
            output_dol = root / 'output.dol'
            output_dat.write_bytes(b'I' * self.ORIGINAL_OFFSET + self.ORIGINAL + b'I' * 16)
            dol = bytearray(192)
            for record, dat_offset in zip((0, 96), output_offsets):
                words = [helper._DAT_FNAME_PTR, len(self.ORIGINAL), dat_offset, len(self.ORIGINAL)] * 3
                struct.pack_into('>12I', dol, record, *words)
            output_dol.write_bytes(dol)
            with (
                mock.patch.object(helper, 'OUTPUT_DAT', str(output_dat)),
                mock.patch.object(helper, 'OUTPUT_DOL', str(output_dol)),
                mock.patch.object(helper, '_readDirPtrs', return_value=[0, 96]),
                mock.patch.object(helper, 'readDolEntry',
                                  return_value=(self.ORIGINAL_OFFSET, len(self.ORIGINAL))),
            ):
                helper.zeroOriginalModel(1, 0)
            data = output_dat.read_bytes()
            return data[self.ORIGINAL_OFFSET:self.ORIGINAL_OFFSET + len(self.ORIGINAL)]

    def test_range_still_routed_by_another_directory_is_kept(self):
        # Dir 1 moved to hammerspace; dir 0 still reads the vanilla block.
        self.assertEqual(self._run((self.ORIGINAL_OFFSET, 0x1000)), self.ORIGINAL)

    def test_route_overlapping_only_part_of_the_range_also_keeps_it(self):
        self.assertEqual(self._run((self.ORIGINAL_OFFSET + 4, 0x1000)), self.ORIGINAL)

    def test_unrouted_range_is_zeroed(self):
        self.assertEqual(self._run((0x2000, 0x1000)), b'\x00' * len(self.ORIGINAL))


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