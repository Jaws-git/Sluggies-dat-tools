"""Roster expansion: DOL hammerspace (Phase 1), the step ledger and the menu [10] runner (Phase 0)."""

import json
import os
import struct
import tempfile
import unittest

from SluggiesTools.Dol import dolfile, ppc
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import ledger, runner, steps

CODE = 0x80595F00          # T0 covers OSInit's arena pairs
CODE_SIZE = 0x200
BSS, BSS_SIZE = 0x80706D00, 0x9E180   # ends at 0x807A4E80, as in the US DOL
ARENA = {0x80595FC4: (3, 0x807B6E80), 0x80596014: (3, 0x807B4E80),
         0x8059606C: (5, 0x807B6E80), 0x805960A0: (3, 0x807B4E80)}


def synthetic_dol() -> bytes:
    words = [0x60000000] * (CODE_SIZE // 4)
    for lis_site, (reg, value) in ARENA.items():
        words[(lis_site - CODE) // 4] = ppc.one(lis_site, lambda a: a.lis(reg, ppc.ha(value)))
        words[(lis_site + 4 - CODE) // 4] = ppc.one(lis_site + 4, lambda a: a.addi(reg, reg, ppc.lo(value)))
    text = struct.pack(f'>{len(words)}I', *words)
    data = b'\x22' * 0x60
    header = bytearray(dolfile.HEADER_SIZE)
    struct.pack_into('>I', header, 0, dolfile.HEADER_SIZE)
    struct.pack_into('>I', header, 0x48, CODE)
    struct.pack_into('>I', header, 0x90, len(text))
    struct.pack_into('>I', header, 7 * 4, dolfile.HEADER_SIZE + len(text))
    struct.pack_into('>I', header, 0x48 + 7 * 4, 0x80631000)
    struct.pack_into('>I', header, 0x90 + 7 * 4, len(data))
    struct.pack_into('>III', header, 0xD8, BSS, BSS_SIZE, CODE)
    return bytes(header) + text + data


class DolHammerspaceTests(unittest.TestCase):
    def test_create_adds_both_sections_and_raises_the_arena(self):
        image = dolfile.DolImage(synthetic_dol())
        hs = dhs.DolHammerspace.create(image)
        text, data = image.section_at(dhs.TEXT_BASE), image.section_at(dhs.DATA_BASE)
        self.assertTrue(text.is_text and not data.is_text)
        self.assertEqual(image.read(dhs.TEXT_BASE, 16), dhs.TEXT_MAGIC)
        self.assertEqual(dhs.arena_values(image), [dhs.DATA_BASE + dhs.MARKER_SIZE] * 4)
        self.assertEqual(hs.arena_low, dhs.DATA_BASE + dhs.MARKER_SIZE)

    def test_reopen_continues_allocating(self):
        image = dolfile.DolImage(synthetic_dol())
        hs = dhs.DolHammerspace.create(image)
        table = hs.data.put(b'\x01' * 0x101)
        stub = hs.code.put(ppc.Asm(hs.code.here).blr().assemble())
        hs.commit()
        again = dhs.DolHammerspace.open(image)
        self.assertEqual(again.data.here, table + 0x101)
        self.assertEqual(again.code.here, stub + 4)
        self.assertEqual(image.read(table, 0x101), b'\x01' * 0x101)
        self.assertEqual(dhs.arena_values(image)[0], dolfile.align_up(table + 0x101))

    def test_commit_keeps_the_file_compact(self):
        image = dolfile.DolImage(synthetic_dol())
        hs = dhs.DolHammerspace.create(image)
        size = len(image.data)
        hs.commit()
        hs.commit()
        self.assertEqual(len(image.data), size)

    def test_refusals(self):
        image = dolfile.DolImage(synthetic_dol())
        dhs.DolHammerspace.create(image)
        with self.assertRaisesRegex(dhs.HammerspaceError, 'already exist'):
            dhs.DolHammerspace.create(image)
        other = dolfile.DolImage(synthetic_dol())
        dhs.set_arena_low(other, 0x807B8000)
        with self.assertRaisesRegex(dhs.HammerspaceError, 'not stock'):
            dhs.DolHammerspace.create(other)
        with self.assertRaisesRegex(dhs.HammerspaceError, 'above'):
            dhs.set_arena_low(other, dhs.ARENA_LO_MAX + 0x20)
        hs = dhs.DolHammerspace.open(image)
        with self.assertRaises(dolfile.DolError):
            hs.data.put(bytes(dhs.ARENA_LO_MAX - hs.data.here + 4))


class LedgerTests(unittest.TestCase):
    def test_diff_and_undo_round_trip_with_tail(self):
        old = bytes(range(256)) * 64
        new = bytearray(old)
        new[10:12] = b'\xff\xff'
        new[5000] = 0
        new += b'tail'
        diff = ledger.diff_bytes(old, bytes(new))
        self.assertEqual(len(diff['ranges']), 2)
        self.assertEqual(ledger.undo_diff(new, diff), 'undone')
        self.assertEqual(bytes(new), old)
        self.assertEqual(ledger.undo_diff(bytearray(old), diff), 'already undone')

    def test_undo_refuses_foreign_changes(self):
        old = bytes(64)
        new = bytearray(old)
        new[0] = 1
        diff = ledger.diff_bytes(old, bytes(new))
        new[0] = 2
        with self.assertRaises(ledger.LedgerError):
            ledger.undo_diff(new, diff)

    def test_regenerated_file_after_overlapping_steps(self):
        # step 2 rewrites a byte step 1 wrote (as every step after dol_hammerspace rewrites the DOL header)
        clean = bytes(64)
        one = bytearray(clean)
        one[4] = 1
        one += b'tail'
        two = bytearray(one)
        two[4] = 2
        two[9] = 9
        diffs = [ledger.diff_bytes(clean, bytes(one)), ledger.diff_bytes(bytes(one), bytes(two))]
        with self.assertRaises(ledger.LedgerError):
            ledger.undo_diff(bytearray(clean), diffs[1])          # the per-step check alone fails
        self.assertTrue(ledger.run_already_undone(clean, diffs))
        self.assertFalse(ledger.run_already_undone(bytes(two), diffs))
        self.assertEqual(ledger.pre_run_ranges([d['ranges'] for d in diffs]), [(4, b'\0'), (5, bytes(5))])  # step 2's range 4-9 minus byte 4

    def test_dat_file_records_and_undoes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'dt_na.dat')
            with open(path, 'wb') as f:
                f.write(bytes(0x100))
            dat = ledger.DatFile(path)
            dat.write(0x10, b'abcd')
            dat.write(0x12, b'XY')
            self.assertEqual(dat.read(0x10, 4), b'abXY')
            records = dat.take_records()
            dat.flush()
            with open(path, 'rb') as f:
                self.assertEqual(f.read()[0x10:0x14], b'abXY')
            dat = ledger.DatFile(path)
            self.assertEqual(dat.undo(records), 'undone')
            dat.flush()
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), bytes(0x100))
            self.assertEqual(ledger.DatFile(path).undo(records), 'already undone')

    def test_run_record_and_undo_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'dt_na.dat')
            with open(path, 'wb') as f:
                f.write(bytes(range(256)))
            dat = ledger.DatFile(path)
            dat.write(0x10, b'AAAA')                      # step 1
            one = dat.take_raw()
            dat.write(0x12, b'BBBB')                      # step 2 overwrites part of step 1
            dat.write(0x10, bytes(range(0x10, 0x12)))     # ... and puts two bytes back
            record = dat.run_record([one, dat.take_raw()])
            started = b''.join(ledger.unpack(old) for _o, old, _sha, _n in sorted(record))
            self.assertEqual((record[0][0], started), (0x10, bytes(range(0x10, 0x16))))   # the run's starting bytes
            dat.flush()
            self.assertEqual(ledger.DatFile(path).read(0x10, 6), b'\x10\x11BBBB')
            dat = ledger.DatFile(path)
            self.assertEqual(dat.undo_run(record), 'undone')
            dat.flush()
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), bytes(range(256)))
            self.assertEqual(ledger.DatFile(path).undo_run(record), 'already undone')
            dat = ledger.DatFile(path)
            dat.write(0x12, b'XX')
            dat.flush()
            with self.assertRaisesRegex(ledger.LedgerError, 'changed since'):
                ledger.DatFile(path).undo_run(record)

    def test_records_are_packed_and_hex_still_reads(self):
        self.assertEqual(ledger.unpack(ledger.pack(bytes(1 << 20))), bytes(1 << 20))
        self.assertLess(len(ledger.pack(bytes(1 << 20))), 2000)          # zero runs cost next to nothing
        self.assertEqual(ledger.unpack('00ff'), b'\0\xff')              # older reports and DOL diffs
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'dt_na.dat')
            with open(path, 'wb') as f:
                f.write(bytes(0x100))
            dat = ledger.DatFile(path)
            dat.write(0x10, b'abcd')
            dat.flush()
            hex_records = [[0x10, bytes(4).hex(), b'abcd'.hex()]]       # a record as older reports wrote it
            dat = ledger.DatFile(path)
            self.assertEqual(dat.undo(hex_records), 'undone')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.dol_path = os.path.join(self.tmp, 'main.dol')
        self.original = synthetic_dol()
        with open(self.dol_path, 'wb') as f:
            f.write(self.original)
        self.config = os.path.join(self.tmp, 'roster.json')
        with open(self.config, 'w') as f:
            json.dump({'version': 1}, f)   # no 'ids' key: only the DOL hammerspace step acts

    def dol(self) -> bytes:
        with open(self.dol_path, 'rb') as f:
            return f.read()

    def test_runs_are_repeatable_and_removable(self):
        report = runner.run(self.tmp, self.config)
        self.assertIn('dol_hammerspace', [s['key'] for s in report['steps']])
        first = self.dol()
        self.assertNotEqual(first, self.original)
        runner.run(self.tmp, self.config)
        self.assertEqual(self.dol(), first)
        runner.run(self.tmp, remove_only=True)
        self.assertEqual(self.dol(), self.original)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, runner.REPORT_NAME)))

    def test_dry_run_writes_nothing(self):
        runner.run(self.tmp, self.config, dry_run=True)
        self.assertEqual(self.dol(), self.original)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, runner.REPORT_NAME)))

    def test_regenerated_dol_counts_as_already_removed(self):
        runner.run(self.tmp, self.config)
        with open(self.dol_path, 'wb') as f:      # e.g. the normal pipeline wrote a fresh DOL
            f.write(self.original)
        report = runner.run(self.tmp, self.config)
        self.assertIn('already undone', ' '.join(report['log']))

    def test_cut_off_report_is_explained(self):
        runner.run(self.tmp, self.config)
        path = os.path.join(self.tmp, runner.REPORT_NAME)
        with open(path, 'r+b') as f:
            f.truncate(os.path.getsize(path) // 2)
        with self.assertRaisesRegex(runner.RosterDevError, 'interrupted run'):
            runner.run(self.tmp, self.config)
        self.assertFalse(os.path.exists(path + '.tmp'))

    def test_cut_off_report_over_clean_files_is_set_aside(self):
        runner.run(self.tmp, self.config)
        path = os.path.join(self.tmp, runner.REPORT_NAME)
        with open(path, 'r+b') as f:
            f.truncate(os.path.getsize(path) // 2)
        with open(self.dol_path, 'wb') as f:       # clean files put back by hand
            f.write(self.original)
        report = runner.run(self.tmp, self.config)
        self.assertIn('set aside', ' '.join(report['log']))
        self.assertTrue(os.path.isfile(path + '.unreadable'))
        self.assertTrue(os.path.isfile(path))

    def test_missing_dol_is_reported(self):
        os.remove(self.dol_path)
        with self.assertRaisesRegex(runner.RosterDevError, 'normal pipeline'):
            runner.run(self.tmp, self.config)

    def test_registry_lists_planned_steps_in_order(self):
        keys = [k for k, _p, _t in steps.PLANNED]
        built = [s.key for s in steps.implemented()]
        self.assertEqual(built, [k for k in keys if k in built])
        self.assertEqual(built[0], 'dol_hammerspace')


if __name__ == '__main__':
    unittest.main()
