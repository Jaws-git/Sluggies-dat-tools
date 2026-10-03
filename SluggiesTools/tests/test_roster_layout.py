"""Roster expansion Phase 2: DAT hammerspace allocation and the select layout copies."""

import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Dol import dolfile
from SluggiesTools.Icons import layout2d
from SluggiesTools.Icons.tests.test_layout2d import make_bank, make_element
from SluggiesTools.Roster import dat_hammerspace as dhs
from SluggiesTools.Roster import layout_file, ledger, runner, steps
from SluggiesTools.tests.test_roster_dev import synthetic_dol

BASE = 0x4000                       # stands in for BASE_SIZE
FNAME = dhs.hh._DAT_FNAME_PTR
RECORDS = 0x80692058                # dir 0: model A (in hammerspace), model B (stock), the layout record
MODEL_A = (BASE, 0x40)
MODEL_B = (0x100, 0x80)
STOCK = {'en': 0x1000, 'sp': 0x2000, 'fr': 0x3000}


def record(slots) -> bytes:
    return b''.join(struct.pack('>4I', FNAME, length, offset, length) for offset, length in slots)


def layouts() -> dict[str, bytes]:
    bank = make_bank([make_element([(1, 2, 0xFF)]), make_element([(3, 4, 0xFF)])])
    out = {}
    for number, lang in enumerate(dhs.LANGS):
        data = bytearray(bank)
        data[0x30] = 0xA0 + number           # the languages differ in their texture data
        out[lang] = bytes(data) + bytes(0x10)    # not a multiple of 32, like the real file
    return out


def synthetic_files(tmp: str) -> tuple[str, str]:
    """A DOL with OSInit's arena pairs and a directory table, and a DAT holding the three layout copies."""
    image = dolfile.DolImage(synthetic_dol())
    blob = bytearray(0xF000)
    base = 0x80692000
    table = dhs.dol_base_address(dhs.hh._DIRS_START)
    pointers = [RECORDS] + [layout_file.RECORD] * (dhs.hh._DIRS_COUNT - 1)
    struct.pack_into(f'>{len(pointers)}I', blob, table - base, *pointers)
    size = len(layouts()['en'])
    blob[RECORDS - base:RECORDS - base + 0x30] = record([MODEL_A] * 3)
    blob[RECORDS - base + 0x30:RECORDS - base + 0x60] = record([MODEL_B] * 3)
    blob[layout_file.RECORD - base:layout_file.RECORD - base + 0x30] = record(
        [(STOCK[lang], size) for lang in dhs.LANGS])
    image.add_section('data', base, bytes(blob))
    dat = bytearray(BASE + 0x100)
    for lang, data in layouts().items():
        dat[STOCK[lang]:STOCK[lang] + len(data)] = data
    dat[BASE:BASE + 0x20] = b'\x11' * 0x20  # model A ends in zeros, which stay reserved
    dol_path, dat_path = os.path.join(tmp, 'main.dol'), os.path.join(tmp, 'dt_na.dat')
    with open(dol_path, 'wb') as f:
        f.write(image.to_bytes())
    with open(dat_path, 'wb') as f:
        f.write(dat)
    with open(os.path.join(tmp, 'fst.bin'), 'wb') as f:
        f.write(bytes(0x18) + struct.pack('>I', len(dat)))
    return dol_path, dat_path


class AllocatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = os.path.join(self.tmp, 'dt_na.dat')

    def dat(self, data: bytes) -> ledger.DatFile:
        with open(self.path, 'wb') as f:
            f.write(data)
        return ledger.DatFile(self.path)

    def test_skips_data_and_reserved_zero_ranges(self):
        data = bytearray(BASE + 0x200)
        data[BASE + 0x10] = 1                                       # block 0 busy
        dat = self.dat(bytes(data))
        self.assertEqual(dhs.find_free(dat, 0x40, [], BASE), BASE + 0x20)
        self.assertEqual(dhs.find_free(dat, 0x40, [(BASE + 0x20, 0x21)], BASE), BASE + 0x60)
        self.assertIsNone(dhs.find_free(dat, 0x200, [], BASE))

    def test_finds_runs_across_scan_chunks(self):
        data = bytearray(BASE + 0x400)
        data[BASE + 0x150] = 1                                     # runs: 0x140 (short), then from 0x160
        dat = self.dat(bytes(data))
        with mock.patch.object(dhs, 'SCAN_CHUNK', 0x100):
            self.assertEqual(dhs.find_free(dat, 0x180, [], BASE), BASE + 0x160)

    def test_grows_from_the_trailing_zero_run(self):
        data = bytearray(BASE + 0x100)
        data[BASE] = 1
        dat = self.dat(bytes(data))
        at = dhs.allocate(dat, 0x200, [], BASE)
        self.assertEqual(at, BASE + 0x20)
        self.assertEqual(dat.size, at + 0x200 + dhs.BUFFER)
        dat.write(at, b'\x01' * 0x200)
        dat.flush()
        self.assertEqual(os.path.getsize(self.path), at + 0x200 + dhs.BUFFER)
        self.assertEqual(dhs.allocate(ledger.DatFile(self.path), 0x20, [], BASE), at + 0x200)

    def test_grows_a_file_without_hammerspace_from_the_base(self):
        dat = self.dat(bytes(0x100))
        self.assertEqual(dhs.allocate(dat, 0x10, [], BASE), BASE)

    def test_undo_after_the_file_was_regenerated(self):
        dat = self.dat(bytes(BASE))
        at = dhs.allocate(dat, 0x40, [], BASE)
        dat.write(at, b'\x05' * 0x40)
        records = dat.run_record([dat.take_raw()])
        dat.flush()
        regenerated = self.dat(bytes(BASE))                         # e.g. menu [1] copied 1_Input again
        self.assertEqual(regenerated.undo_run(records), 'already undone')


class LayoutStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(dhs, 'BASE_SIZE', BASE))
        self.dol_path, self.dat_path = synthetic_files(self.tmp)
        self.config = os.path.join(self.tmp, 'roster.json')
        with open(self.config, 'w') as f:
            json.dump({'version': 1}, f)

    def files(self) -> tuple[bytes, bytes]:
        with open(self.dol_path, 'rb') as dol, open(self.dat_path, 'rb') as dat:
            return dol.read(), dat.read()

    def context(self) -> steps.RosterContext:
        dol, _dat = self.files()
        return steps.RosterContext(dol=dolfile.DolImage(dol), dat=ledger.DatFile(self.dat_path), config={})

    def test_identity_copies_one_per_language(self):
        ctx = self.context()
        lines = layout_file.apply(ctx)
        copies = layout_file.get(ctx).copies
        self.assertEqual(len({offset for offset, _n in copies.values()}), 3)
        spans = sorted((o, o + n) for o, n in copies.values()) + [(MODEL_A[0], MODEL_A[0] + MODEL_A[1])]
        spans.sort()
        for (_a, end), (start, _b) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start)                       # no overlap, model A's zero tail kept
        for lang, data in layouts().items():
            offset, length = copies[lang]
            self.assertGreaterEqual(offset, BASE)
            self.assertEqual(offset % 32, 0)
            self.assertEqual(ctx.dat.read(offset, length), data)
            self.assertEqual(ctx.dat.read(STOCK[lang], length), data)  # the stock copy stays
        words = dhs.read_record(ctx.dol, layout_file.RECORD)
        self.assertEqual([dhs.slot(words, lang)[2] for lang in dhs.LANGS],
                         [copies[lang][1] for lang in dhs.LANGS])  # alloc = length
        self.assertIn('game heap unchanged', lines[-1])

    def test_shared_copy_is_copied_once(self):
        ctx = self.context()
        words = dhs.read_record(ctx.dol, layout_file.RECORD)
        dhs.set_slot(words, 'fr', *dhs.slot(words, 'sp')[:2])
        dhs.write_record(ctx.dol, layout_file.RECORD, words)
        lines = layout_file.apply(ctx)
        copies = layout_file.get(ctx).copies
        self.assertEqual(copies['fr'], copies['sp'])
        self.assertIn('shares', lines[2])

    def test_refuses_a_layout_moved_by_another_tool(self):
        ctx = self.context()
        words = dhs.read_record(ctx.dol, layout_file.RECORD)
        dhs.set_slot(words, 'en', BASE + 0x40, 0x10)
        dhs.write_record(ctx.dol, layout_file.RECORD, words)
        with self.assertRaisesRegex(layout_file.LayoutFileError, 'another tool'):
            layout_file.apply(ctx)

    def test_update_in_place_and_moved(self):
        ctx = self.context()
        layout_file.apply(ctx)
        files = layout_file.get(ctx)
        before = files.copies

        def add_row(_lang, data):
            lay = layout2d.Layout(data)
            lay.add_row(1, (0.0, 0.0, 1.0, 1.0))
            return lay.to_bytes()
        log = files.update(add_row)
        after = files.copies
        self.assertEqual(len(log), 3)
        for lang in dhs.LANGS:
            self.assertNotEqual(after[lang][0], before[lang][0])
            self.assertEqual(after[lang][1], before[lang][1] + layout2d.RESOURCE_ROW_SIZE)
            self.assertEqual(files.read(lang), add_row(lang, layouts()[lang]))
        spans = sorted((o, o + n) for o, n in after.values())
        for (_a, end), (start, _b) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start)
        self.assertIn('game heap +20 bytes', log[0])
        moved = files.copies
        files.update(lambda _lang, data: layouts()['en'])           # back to the stock size: in place
        self.assertEqual({k: v[0] for k, v in files.copies.items()}, {k: v[0] for k, v in moved.items()})

    def test_runner_repeatable_and_removable(self):
        dol0, dat0 = self.files()
        report = runner.run(self.tmp, self.config)
        self.assertEqual([s['key'] for s in report['steps']][:2], ['dol_hammerspace', 'layout_file'])
        dol1, dat1 = self.files()
        self.assertGreater(len(dat1), len(dat0))
        with open(os.path.join(self.tmp, 'fst.bin'), 'rb') as f:
            self.assertEqual(struct.unpack_from('>I', f.read(), 0x14)[0], len(dat1))
        runner.run(self.tmp, self.config)
        self.assertEqual(self.files(), (dol1, dat1))
        runner.run(self.tmp, remove_only=True)
        dol2, dat2 = self.files()
        self.assertEqual(dol2, dol0)
        self.assertEqual(dat2[:len(dat0)], dat0)
        self.assertEqual(dat2[len(dat0):], bytes(len(dat2) - len(dat0)))  # grown, not shrunk (accepted)

    def test_runner_after_regenerated_files(self):
        dol0, dat0 = self.files()
        runner.run(self.tmp, self.config)
        with open(self.dol_path, 'wb') as f:
            f.write(dol0)
        with open(self.dat_path, 'wb') as f:
            f.write(dat0)
        report = runner.run(self.tmp, self.config)
        self.assertIn('already undone', ' '.join(report['log']))


if __name__ == '__main__':
    unittest.main()
