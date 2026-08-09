import io
import pathlib
import struct
import sys
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from model0 import Archive, ExpectedFormatSkip, Model0


def _archive_data(offsets, length):
    header = struct.pack(f'>{len(offsets) + 1}I', len(offsets), *offsets)
    return io.BytesIO(header.ljust(length, b'\x00'))


class ArchiveTests(unittest.TestCase):
    def test_sparse_offsets_preserve_slots_and_use_next_populated_end(self):
        archive = Archive(_archive_data([0x10, 0, 0x28], 0x40), 0, 0x40, 'archive')

        with mock.patch.object(Model0, 'analyze', autospec=True):
            archive.analyze()

        self.assertEqual(archive.fileCount, 3)
        self.assertEqual(len(archive.files), 3)
        self.assertIsNone(archive.files[1])
        self.assertEqual((archive.files[0].offset, archive.files[0].length), (0x10, 0x18))
        self.assertEqual((archive.files[2].offset, archive.files[2].length), (0x28, 0x18))
        self.assertEqual(archive.success, [0, 2])

    def test_descending_populated_offsets_are_rejected(self):
        archive = Archive(_archive_data([0x28, 0, 0x10], 0x40), 0, 0x40, 'archive')

        with self.assertRaisesRegex(ExpectedFormatSkip, 'unsupported archive layout'):
            archive.analyze()


if __name__ == '__main__':
    unittest.main()