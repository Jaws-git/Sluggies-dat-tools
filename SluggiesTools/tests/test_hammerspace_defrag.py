import hashlib
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
if str(HAMMERSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(HAMMERSPACE_DIR))

import HammerspaceHelper as helper
import Defrag

BASE = 0x1000
OWN_DIR_START = 3 * 48                    # dir 1 (an own model directory of a new ID) follows dir 0's records


def record(*slots):
    """One 48-byte directory record from ``(offset, length)`` per language (en, fr, sp)."""
    words = []
    for offset, length in slots:
        words += [helper._DAT_FNAME_PTR, length, offset, length]
    return struct.pack('>12I', *words)


def same(offset, length):
    return record((offset, length), (offset, length), (offset, length))


class Fixture:
    """Output DOL/DAT/FST with a fragmented hammerspace:

    0x1100+0x50  block A (record 0,0) and its sharer (record 0,1)
    0x1200+0x40  unrouted leftover bytes
    0x1300+0x40  record 0,2 en, overlapped by its fr slot 0x1320+0x40 (one extent 0x1300..0x1360); sp vanilla
    0x1400+0x33  record 1,0 (own model directory)
    zeros up to 0x2000
    """

    def __init__(self, root, extra_dol=b''):
        self.root = pathlib.Path(root)
        self.dat = self.root / 'dt_na.dat'
        self.dol = self.root / 'main.dol'
        self.input_dol = self.root / 'input.dol'
        self.fst = self.root / 'fst.bin'
        data = bytearray(b'V' * BASE + bytes(0x1000))
        self.blocks = {0x1100: os.urandom(0x50), 0x1300: os.urandom(0x60), 0x1400: os.urandom(0x33)}
        for offset, block in self.blocks.items():
            data[offset:offset + len(block)] = block
        data[0x1200:0x1240] = b'L' * 0x40
        self.dat.write_bytes(bytes(data))
        dol = (same(0x1100, 0x50) + same(0x1100, 0x50)
               + record((0x1300, 0x40), (0x1320, 0x40), (0x0100, 0x10))
               + same(0x1400, 0x33) + bytes(48) + extra_dol)
        self.dol.write_bytes(dol)
        self.input_dol.write_bytes(dol)
        fst = bytearray(32)
        struct.pack_into('>I', fst, helper._FST_DAT_SIZE_OFF, len(data))
        self.fst.write_bytes(bytes(fst))

    def patches(self):
        return [
            mock.patch.object(helper, 'OUTPUT_DAT', str(self.dat)),
            mock.patch.object(helper, 'OUTPUT_DOL', str(self.dol)),
            mock.patch.object(helper, 'INPUT_DOL', str(self.input_dol)),
            mock.patch.object(helper, '_FST_OUTPUT', str(self.fst)),
            mock.patch.object(helper, '_FST_INPUT', str(self.fst)),
            mock.patch.object(helper, 'BASE_SIZE', BASE),
            mock.patch.object(helper, 'HS_ALIGN_BYTES', 32),
            mock.patch.object(helper, 'HS_BUFFER_BYTES', 0x80),
            mock.patch.object(helper, '_readDirPtrs', return_value=[0]),
            mock.patch.object(helper, '_extraDirStarts', return_value=[OWN_DIR_START]),
        ]

    def snapshot(self):
        return self.dat.read_bytes(), self.dol.read_bytes(), self.fst.read_bytes()

    def routes(self):
        return {(r.chunk, r.index, r.slot): (r.offset, r.length) for r in Defrag.collect_routes()}

    def read(self, offset, length):
        with open(self.dat, 'rb') as f:
            f.seek(offset)
            return f.read(length)


class DefragTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = Fixture(self.temp.name)
        stack = ExitStack()
        for patch in self.fx.patches():
            stack.enter_context(patch)
        self.addCleanup(stack.close)

    def test_packs_blocks_rewrites_routes_and_truncates(self):
        before = {key: hashlib.sha1(self.fx.read(*route)).digest() for key, route in self.fx.routes().items()}
        report = Defrag.run()

        self.assertEqual(report.moved, 3)
        self.assertEqual(report.leftover_bytes, 0x40)
        routes = self.fx.routes()
        self.assertEqual(routes[(0, 0, 0)], (0x1000, 0x50))
        self.assertEqual(routes[(0, 1, 2)], (0x1000, 0x50))                 # the sharer follows its block
        self.assertEqual(routes[(0, 2, 0)], (0x1060, 0x40))                 # aligned after block A
        self.assertEqual(routes[(0, 2, 1)], (0x1080, 0x40))                 # keeps its place inside the extent
        self.assertNotIn((0, 2, 2), routes)                                 # the vanilla slot is untouched
        self.assertEqual(routes[(1, 0, 0)], (0x10C0, 0x33))
        for key, route in routes.items():
            self.assertEqual(hashlib.sha1(self.fx.read(*route)).digest(), before[key])
        size = 0x10C0 + 0x33 + 0x80
        self.assertEqual(os.path.getsize(self.fx.dat), size)
        self.assertEqual(report.size_after, size)
        self.assertEqual(struct.unpack_from('>I', self.fx.fst.read_bytes(), helper._FST_DAT_SIZE_OFF)[0], size)
        data = self.fx.dat.read_bytes()
        self.assertEqual(data[:BASE], b'V' * BASE)
        self.assertNotIn(b'L' * 4, data)                                    # the leftover is gone
        self.assertEqual(data[0x1050:0x1060], bytes(0x10))                  # alignment gap zeroed
        self.assertEqual(data[0x10F3:], bytes(0x80))
        self.assertFalse(os.path.exists(os.path.join(self.temp.name, Defrag.BACKUP_FOLDER)))

    def test_second_run_finds_nothing_to_do(self):
        Defrag.run()
        files = self.fx.snapshot()

        report = Defrag.run()

        self.assertEqual((report.moved, report.size_before, report.size_after), (0, len(files[0]), len(files[0])))
        self.assertEqual(self.fx.snapshot(), files)

    def test_dry_run_writes_nothing(self):
        files = self.fx.snapshot()

        report = Defrag.run(dry_run=True)

        self.assertTrue(report.dry_run)
        self.assertEqual(report.moved, 3)
        self.assertEqual(report.size_after, 0x10C0 + 0x33 + 0x80)
        self.assertEqual(self.fx.snapshot(), files)

    def test_failure_restores_every_file(self):
        files = self.fx.snapshot()

        with mock.patch.object(Defrag, '_patch_dol', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                Defrag.run()

        self.assertEqual(self.fx.snapshot(), files)
        self.assertFalse(os.path.exists(os.path.join(self.temp.name, Defrag.BACKUP_FOLDER)))

    def test_interrupted_run_is_restored_first(self):
        files = self.fx.snapshot()
        layout = Defrag.analyze()
        Defrag._write_backup(layout)
        Defrag._move_extents(layout)                                       # then the process died

        report = Defrag.run()

        self.assertTrue(report.restored)
        self.assertEqual(self.fx.snapshot(), files)

    def test_truncates_trailing_zeros_without_moving(self):
        Defrag.run()
        packed = self.fx.snapshot()
        with open(self.fx.dat, 'ab') as f:
            f.write(bytes(0x4000))                                           # e.g. the untangle step's growth

        report = Defrag.run()

        self.assertEqual(report.moved, 0)
        self.assertEqual(self.fx.snapshot()[0], packed[0])

    def test_refuses_a_route_that_crosses_base_size(self):
        dol = bytearray(self.fx.dol.read_bytes())
        dol[0:48] = same(BASE - 0x10, 0x40)
        self.fx.dol.write_bytes(bytes(dol))
        files = self.fx.snapshot()

        with self.assertRaises(Defrag.DefragError):
            Defrag.run()
        self.assertEqual(self.fx.snapshot(), files)


class UnknownPointerTests(unittest.TestCase):
    def test_warns_about_a_dol_word_naming_a_moving_block(self):
        with tempfile.TemporaryDirectory() as temp:
            fx = Fixture(temp, extra_dol=struct.pack('>I', 0x1300))
            with ExitStack() as stack:
                for patch in fx.patches():
                    stack.enter_context(patch)
                report = Defrag.run(dry_run=True)

        self.assertEqual(len(report.warnings), 1)
        self.assertIn(f'0x{5 * 48:X}', report.warnings[0])


if __name__ == '__main__':
    unittest.main()
