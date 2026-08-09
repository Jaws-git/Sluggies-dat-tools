import pathlib
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


if __name__ == '__main__':
    unittest.main()