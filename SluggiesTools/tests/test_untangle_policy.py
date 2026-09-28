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

import HammerspaceHelper as hh
import UntanglePolicy as policy

RECORD = hh._ENTRY_SIZE
EMPTY = 0x10        # zeroed record every directory without files points at
OWNER_DIR = 24
SECOND_OWNER_DIR = 30
PLACEHOLDER = 0x4B33140


class UntanglePolicyTests(unittest.TestCase):
    """A synthetic input DOL shaped like the vanilla sharing.

    Dir 24 owns three blocks. Dir 89 routes to two of them and has one empty
    route. Dir 90 starts right after dir 89's records: an unbounded walk
    would read its records as (89, 3..5). Dir 90 shares one block with 24,
    has one block of its own, and shares a placeholder with dir 30.
    """

    def setUp(self):
        layout = {
            OWNER_DIR: [(0xA000, 0x100), (0xB000, 0x100), (0xC000, 0x100)],
            89: [(0xA000, 0x100), (0xB000, 0x100), (0, 0)],
            90: [(0xA000, 0x100), (0xD000, 0x80), (PLACEHOLDER, 0x40)],
            SECOND_OWNER_DIR: [(PLACEHOLDER, 0x40)],
        }
        # Records are laid out in this order, back to back; 89 and 90 are
        # contiguous so their bounds come only from the directory pointers.
        order = [OWNER_DIR, SECOND_OWNER_DIR, 89, 90]
        dol = bytearray(0x100)
        pointers = [EMPTY] * 95
        for chunk in order:
            pointers[chunk] = len(dol)
            for offset, length in layout[chunk]:
                dol += struct.pack('>12I', *([hh._DAT_FNAME_PTR, length, offset, length] * 3))
            if chunk != 89:
                dol += b'\x00' * RECORD
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        dol_path = pathlib.Path(temp.name) / 'main.dol'
        dol_path.write_bytes(dol)
        for patcher in (
            mock.patch.object(hh, 'INPUT_DOL', str(dol_path)),
            mock.patch.object(hh, '_readDirPtrs', return_value=pointers),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_directory_list(self):
        self.assertEqual(policy.UNUSED_CHARACTER_DIRS, (89, 90, 91, 92, 93, 94))
        self.assertTrue(policy.is_split_dir(89))
        self.assertFalse(policy.is_split_dir(OWNER_DIR))

    def test_split_routes_skip_empty_routes_and_stay_inside_each_directory(self):
        self.assertEqual(
            policy.split_routes(),
            [(89, 0), (89, 1), (90, 0), (90, 1), (90, 2)],
        )

    def test_is_split(self):
        self.assertTrue(policy.is_split(89, 0))
        self.assertTrue(policy.is_split(90, 1))
        self.assertFalse(policy.is_split(89, 2))    # empty route
        self.assertFalse(policy.is_split(89, 3))    # dir 90's first record
        self.assertFalse(policy.is_split(OWNER_DIR, 0))

    def test_owner_routes_exclude_other_split_routes(self):
        self.assertEqual(policy.owner_routes(89, 0), [(OWNER_DIR, 0)])
        self.assertEqual(policy.owner_routes(90, 0), [(OWNER_DIR, 0)])
        self.assertEqual(policy.owner_routes(89, 1), [(OWNER_DIR, 1)])

    def test_owner_routes_of_own_block_placeholder_and_non_split_routes(self):
        self.assertEqual(policy.owner_routes(90, 1), [])
        self.assertEqual(policy.owner_routes(90, 2), [(SECOND_OWNER_DIR, 0)])
        self.assertEqual(policy.owner_routes(89, 2), [])
        self.assertEqual(policy.owner_routes(OWNER_DIR, 0), [])


if __name__ == '__main__':
    unittest.main()
