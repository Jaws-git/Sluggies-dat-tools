"""Roster expansion: DOL hammerspace, buffered DAT writes and the menu [7] runner with its reset to vanilla."""

import json
import os
import struct
import tempfile
import unittest

from SluggiesTools.Dol import dolfile, ppc
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import datfile, runner, steps

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


class DatFileTests(unittest.TestCase):
    def test_buffered_writes_and_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'dt_na.dat')
            with open(path, 'wb') as f:
                f.write(bytes(0x100))
            dat = datfile.DatFile(path)
            dat.write(0x10, b'abcd')
            dat.write(0x12, b'XY')
            self.assertEqual(dat.read(0x10, 4), b'abXY')
            dat.grow(0x140)
            self.assertEqual(dat.read(0x130, 0x10), bytes(0x10))
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), bytes(0x100))          # nothing on disk before flush
            dat.flush()
            with open(path, 'rb') as f:
                data = f.read()
            self.assertEqual((len(data), data[0x10:0x14]), (0x140, b'abXY'))
            with self.assertRaises(datfile.DatFileError):
                datfile.DatFile(path).read(0x130, 0x20)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.input_dir = os.path.join(self.tmp, '1_Input')
        os.makedirs(self.input_dir)
        self.dol_path = os.path.join(self.tmp, 'main.dol')
        self.original = synthetic_dol()
        for path in (self.dol_path, os.path.join(self.input_dir, 'main.dol')):
            with open(path, 'wb') as f:
                f.write(self.original)
        self.config = os.path.join(self.tmp, 'roster.json')
        with open(self.config, 'w') as f:
            json.dump({'version': 1}, f)   # no 'ids' key: only the DOL hammerspace step acts

    def run_roster(self, **kwargs) -> dict:
        kwargs.setdefault('config_path', None if kwargs.get('remove_only') else self.config)
        return runner.run(self.tmp, input_dir=self.input_dir, **kwargs)

    def dol(self) -> bytes:
        with open(self.dol_path, 'rb') as f:
            return f.read()

    def test_runs_are_repeatable_and_resettable(self):
        report = self.run_roster()
        self.assertIn('dol_hammerspace', [s['key'] for s in report['steps']])
        first = self.dol()
        self.assertNotEqual(first, self.original)
        self.run_roster()
        self.assertEqual(self.dol(), first)
        self.run_roster(remove_only=True)
        self.assertEqual(self.dol(), self.original)
        report = self.run_roster(remove_only=True)
        self.assertIn('no roster changes', ' '.join(report['log']))

    def test_dry_run_writes_nothing(self):
        self.run_roster(dry_run=True)
        self.assertEqual(self.dol(), self.original)

    def test_reset_needs_no_record_of_earlier_runs(self):
        self.run_roster()
        legacy = os.path.join(self.tmp, runner.LEGACY_REPORT_DIR)
        os.makedirs(legacy)
        with open(os.path.join(legacy, 'report.json'), 'w') as f:
            f.write('{"cut off')                            # an old, broken undo report
        report = self.run_roster(remove_only=True)
        self.assertEqual(self.dol(), self.original)
        self.assertFalse(os.path.exists(legacy))
        self.assertIn('no longer needed', ' '.join(report['log']))

    def test_refusals(self):
        self.run_roster()
        with open(os.path.join(self.input_dir, 'main.dol'), 'wb') as f:
            f.write(self.dol())                              # an injected DOL as the reference
        with self.assertRaisesRegex(runner.RosterDevError, 'already holds a roster injection'):
            self.run_roster()
        os.remove(os.path.join(self.input_dir, 'main.dol'))
        with self.assertRaisesRegex(runner.RosterDevError, 'needs the original'):
            self.run_roster()

    def test_other_game_files_are_refused(self):
        with open(os.path.join(self.input_dir, 'main.dol'), 'wb') as f:
            f.write(self.original + bytes(0x20))
        with self.assertRaisesRegex(runner.RosterDevError, 'not built from'):
            self.run_roster()

    def test_missing_dol_is_reported(self):
        os.remove(self.dol_path)
        with self.assertRaisesRegex(runner.RosterDevError, 'normal pipeline'):
            self.run_roster()

    def test_registry_runs_every_step_in_order(self):
        self.assertEqual([s.key for s in steps.all_steps()], [k for k, _t in steps.STEPS])
        self.assertEqual(steps.all_steps()[0].key, 'dol_hammerspace')

    def test_config_is_required(self):
        with self.assertRaisesRegex(runner.RosterDevError, 'no roster configuration'):
            runner.run(self.tmp, input_dir=self.input_dir)


if __name__ == '__main__':
    unittest.main()
