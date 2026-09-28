"""Patch / unpatch cycles on split routes and their owner (untangler Phase 3).

A synthetic input: dir 24 (the owner) and dirs 89 and 90 (two unused
characters) all route file 0 to one vanilla block, as Yoshi and the two unused
Yoshis do. Everything runs on real HammerspaceHelper file I/O in a temp
directory; only the LOD partner hooks and debug dumps are stubbed out.
"""
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import HammerspaceHelper as hh
import HammerspaceMain as main
import UntanglePolicy as policy

OWNER = (24, 0)
SPLIT_A = (89, 0)
SPLIT_B = (90, 0)
BASE_SIZE = 0x4000
VANILLA_OFFSET = 0x1000
VANILLA = bytes(range(256)) * 2          # 0x200 bytes
EMPTY_PTR = 0x10


class UntangleCycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name)

        dol = bytearray(0x100)
        pointers = [EMPTY_PTR] * 95
        self.records = {}
        for chunk in (OWNER[0], SPLIT_A[0], SPLIT_B[0]):
            pointers[chunk] = len(dol)
            self.records[(chunk, 0)] = len(dol)
            dol += struct.pack('>12I', *([hh._DAT_FNAME_PTR, len(VANILLA), VANILLA_OFFSET, len(VANILLA)] * 3))
            dol += b'\x00' * hh._ENTRY_SIZE
        dat = bytearray(BASE_SIZE)
        dat[VANILLA_OFFSET:VANILLA_OFFSET + len(VANILLA)] = VANILLA

        self.input_dol, self.input_dat = root / 'in.dol', root / 'in.dat'
        self.output_dol, self.output_dat = root / 'out.dol', root / 'out.dat'
        self.input_dol.write_bytes(dol)
        self.input_dat.write_bytes(dat)
        # The output starts re-tangled: exactly the input.
        shutil.copyfile(self.input_dol, self.output_dol)
        shutil.copyfile(self.input_dat, self.output_dat)

        patches = [
            mock.patch.object(hh, 'INPUT_DOL', str(self.input_dol)),
            mock.patch.object(hh, 'INPUT_DAT', str(self.input_dat)),
            mock.patch.object(hh, 'OUTPUT_DOL', str(self.output_dol)),
            mock.patch.object(hh, 'OUTPUT_DAT', str(self.output_dat)),
            mock.patch.object(hh, '_FST_INPUT', str(root / 'missing_fst.bin')),
            mock.patch.object(hh, '_FST_OUTPUT', str(root / 'fst.bin')),
            mock.patch.object(hh, 'BASE_SIZE', BASE_SIZE),
            mock.patch.object(hh, '_readDirPtrs', return_value=pointers),
            mock.patch.object(hh, 'writeDebugDumps'),
            mock.patch.object(main, 'CheckLodPartner'),
            mock.patch.object(main.LodTextureSync, 'sync_low_block', side_effect=lambda block, *_a, **_k: block),
            mock.patch.object(main.LodTextureSync, 'sync_partner_of_high'),
            mock.patch.object(main.LodPartnerGuard, 'read_current_block', return_value=None),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- helpers ----------------------------------------------------------

    def route(self, route):
        words = struct.unpack_from('>12I', self.output_dol.read_bytes(), self.records[route])
        return words[2], words[1]

    def bytes_at(self, offset, length):
        with open(self.output_dat, 'rb') as dat:
            dat.seek(offset)
            return dat.read(length)

    def snapshot(self, route):
        offset, length = self.route(route)
        return offset, length, self.bytes_at(offset, length)

    def patch(self, route, payload):
        build = main.ModelBlockBuild(
            block=payload, parsed=None, chunk_number=route[0], file_index=route[1],
            original_offset=VANILLA_OFFSET, original_length=len(VANILLA),
            section_modes=main.SectionModes(), section_sizes={},
            validation_report={'valid': True},
        )
        return main.WriteModelBlock(build, f'{route}.sluggie')

    def unpatch(self, route):
        success, _offset, _length = hh.removeModelFromHammerspace(*route)
        self.assertTrue(success)

    def assert_split_baseline(self, route):
        offset, length = self.route(route)
        self.assertGreaterEqual(offset, BASE_SIZE, f'{route} is back on the shared block')
        self.assertEqual(self.bytes_at(offset, length), VANILLA)

    def assert_vanilla_intact(self):
        self.assertEqual(self.bytes_at(VANILLA_OFFSET, len(VANILLA)), VANILLA)

    def resplit(self):
        repaired, failed = policy.resplit_retangled(find_model=lambda *_route: None)
        self.assertEqual((repaired, failed), ([SPLIT_A, SPLIT_B], []))

    # -- re-split ------------------------------------------------------------

    def test_resplit_gives_each_unused_route_its_own_copy_and_leaves_the_owner(self):
        self.assertEqual(policy.retangled_routes(), [SPLIT_A, SPLIT_B])
        self.resplit()

        self.assert_split_baseline(SPLIT_A)
        self.assert_split_baseline(SPLIT_B)
        self.assertNotEqual(self.route(SPLIT_A)[0], self.route(SPLIT_B)[0])
        self.assertEqual(self.route(OWNER), (VANILLA_OFFSET, len(VANILLA)))
        self.assert_vanilla_intact()
        self.assertEqual(policy.retangled_routes(), [])
        self.assertEqual(policy.resplit_retangled(find_model=lambda *_route: None), ([], []))

    # -- split route cycle ---------------------------------------------------

    def test_patch_unpatch_patch_of_a_split_route_never_touches_the_others(self):
        self.resplit()
        owner, other = self.snapshot(OWNER), self.snapshot(SPLIT_B)
        first_copy = self.route(SPLIT_A)

        edited = b'P' * 0x240
        self.patch(SPLIT_A, edited)
        self.assertEqual(self.snapshot(SPLIT_A)[2], edited)
        self.assertEqual(self.bytes_at(*first_copy), b'\x00' * first_copy[1])
        self.assertEqual((self.snapshot(OWNER), self.snapshot(SPLIT_B)), (owner, other))
        self.assert_vanilla_intact()

        patched_block = self.route(SPLIT_A)
        self.unpatch(SPLIT_A)
        self.assert_split_baseline(SPLIT_A)
        self.assertEqual(self.bytes_at(*patched_block), b'\x00' * patched_block[1])
        self.assertEqual((self.snapshot(OWNER), self.snapshot(SPLIT_B)), (owner, other))
        self.assert_vanilla_intact()

        self.patch(SPLIT_A, b'Q' * 0x200)
        self.assertEqual(self.snapshot(SPLIT_A)[2], b'Q' * 0x200)
        self.assertEqual((self.snapshot(OWNER), self.snapshot(SPLIT_B)), (owner, other))
        self.assert_vanilla_intact()

    def test_unpatch_of_an_untouched_split_route_changes_nothing(self):
        self.resplit()
        before = (self.output_dol.read_bytes(), self.output_dat.read_bytes())
        self.unpatch(SPLIT_A)
        self.assertEqual((self.output_dol.read_bytes(), self.output_dat.read_bytes()), before)

    def test_patch_of_a_retangled_split_route_moves_only_that_route(self):
        # Output as an older unpatch left it: every route on the vanilla block.
        self.patch(SPLIT_A, b'P' * 0x200)

        self.assertGreaterEqual(self.route(SPLIT_A)[0], BASE_SIZE)
        self.assertEqual(self.route(OWNER), (VANILLA_OFFSET, len(VANILLA)))
        self.assertEqual(self.route(SPLIT_B), (VANILLA_OFFSET, len(VANILLA)))
        self.assert_vanilla_intact()

    def test_unpatch_of_a_retangled_split_route_resplits_it(self):
        self.unpatch(SPLIT_A)

        self.assert_split_baseline(SPLIT_A)
        self.assertEqual(self.route(OWNER), (VANILLA_OFFSET, len(VANILLA)))
        self.assertEqual(self.route(SPLIT_B), (VANILLA_OFFSET, len(VANILLA)))
        self.assert_vanilla_intact()

    # -- owner cycle -----------------------------------------------------------

    def test_patch_unpatch_patch_of_the_owner_never_drags_split_routes(self):
        self.resplit()
        split_a, split_b = self.snapshot(SPLIT_A), self.snapshot(SPLIT_B)

        self.patch(OWNER, b'O' * 0x200)
        self.assertEqual(self.snapshot(OWNER)[2], b'O' * 0x200)
        self.assertEqual((self.snapshot(SPLIT_A), self.snapshot(SPLIT_B)), (split_a, split_b))

        self.unpatch(OWNER)
        self.assertEqual(self.route(OWNER), (VANILLA_OFFSET, len(VANILLA)))
        self.assert_vanilla_intact()
        self.assertEqual((self.snapshot(SPLIT_A), self.snapshot(SPLIT_B)), (split_a, split_b))

        self.patch(OWNER, b'R' * 0x200)
        self.assertEqual((self.snapshot(SPLIT_A), self.snapshot(SPLIT_B)), (split_a, split_b))

    def test_owner_patch_on_a_retangled_output_keeps_the_vanilla_block_for_split_routes(self):
        self.patch(OWNER, b'O' * 0x200)

        self.assertGreaterEqual(self.route(OWNER)[0], BASE_SIZE)
        self.assertEqual(self.route(SPLIT_A), (VANILLA_OFFSET, len(VANILLA)))
        self.assertEqual(self.route(SPLIT_B), (VANILLA_OFFSET, len(VANILLA)))
        self.assert_vanilla_intact()


class IndependentSharersTests(unittest.TestCase):
    def test_split_routes_move_alone(self):
        self.assertEqual(policy.independent_sharers(89, 0, [(24, 0), (90, 0)]), [])

    def test_owner_keeps_only_non_split_sharers(self):
        self.assertEqual(
            policy.independent_sharers(24, 0, [(89, 0), (90, 0), (60, 3)]),
            [(60, 3)],
        )


if __name__ == '__main__':
    unittest.main()
