"""Game options (menu [8]): CPU vs CPU hooks in main.dol, their runner, and survival of roster runs."""

import json
import os
import struct
import tempfile
import unittest

from SluggiesTools.Dol import dolfile, ppc
from SluggiesTools.GameOptions import game_options as go
from SluggiesTools.GameOptions import runner as options_runner
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import runner as roster_runner
from SluggiesTools.tests.test_roster_dev import ARENA, BSS, BSS_SIZE, CODE, CODE_SIZE

HOOK_SITES = {h.site: h.stock for o in go.OPTIONS for h in o.hooks}


def synthetic_dol() -> bytes:
    """test_roster_dev's DOL (T0 with OSInit's arena pairs) plus one small text section per hook site."""
    sections = []
    words = [0x60000000] * (CODE_SIZE // 4)
    for lis_site, (reg, value) in ARENA.items():
        words[(lis_site - CODE) // 4] = ppc.one(lis_site, lambda a: a.lis(reg, ppc.ha(value)))
        words[(lis_site + 4 - CODE) // 4] = ppc.one(lis_site + 4, lambda a: a.addi(reg, reg, ppc.lo(value)))
    sections.append((CODE, words))
    for site, stock in sorted(HOOK_SITES.items()):
        base = site - 0x10
        block = [0x60000000] * 8
        block[4] = stock
        sections.append((base, block))
    header = bytearray(dolfile.HEADER_SIZE)
    body = b''
    for slot, (address, block) in enumerate(sections):
        blob = struct.pack(f'>{len(block)}I', *block)
        struct.pack_into('>I', header, slot * 4, dolfile.HEADER_SIZE + len(body))
        struct.pack_into('>I', header, 0x48 + slot * 4, address)
        struct.pack_into('>I', header, 0x90 + slot * 4, len(blob))
        body += blob
    data = b'\x22' * 0x60
    struct.pack_into('>I', header, 7 * 4, dolfile.HEADER_SIZE + len(body))
    struct.pack_into('>I', header, 0x48 + 7 * 4, 0x80631000)
    struct.pack_into('>I', header, 0x90 + 7 * 4, len(data))
    struct.pack_into('>III', header, 0xD8, BSS, BSS_SIZE, CODE)
    return bytes(header) + body + data


def stub_of(image: dolfile.DolImage, site: int) -> int:
    return ppc.branch_target(image.u32(site), site)


class GameOptionTests(unittest.TestCase):
    def setUp(self):
        self.image = dolfile.DolImage(synthetic_dol())

    def test_apply_detect_remove(self):
        self.assertEqual(go.detect(self.image), [])
        go.apply(self.image, ['cpu_vs_cpu', 'cpu_management'])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu', 'cpu_management'])
        for site in HOOK_SITES:
            self.assertTrue(dhs.TEXT_BASE <= stub_of(self.image, site) < dhs.TEXT_LIMIT)
        go.remove(self.image, ['cpu_management'])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu'])
        for h in go.BY_KEY['cpu_management'].hooks:
            self.assertEqual(self.image.u32(h.site), h.stock)

    def test_stubs_replay_the_stock_word_and_return(self):
        go.apply(self.image, ['cpu_vs_cpu', 'cpu_management'])
        for site, stock in HOOK_SITES.items():
            stub = stub_of(self.image, site)
            words = [self.image.u32(stub + i) for i in range(0, 0x40, 4)]
            back = next(i for i, w in enumerate(words) if ppc.branch_target(w, stub + i * 4) == site + 4)
            self.assertEqual(words[back - 1], stock, hex(site))

    def test_management_writes_through_r13_globals(self):
        go.apply(self.image, ['cpu_management'])
        flip = stub_of(self.image, go.FLIP_SITE)
        words = [self.image.u32(flip + i) for i in range(0, 0x2C, 4)]
        self.assertIn(ppc.one(0, lambda a: a.lwz(12, go.TEAM_MAP, 13)), words)
        self.assertIn(ppc.one(0, lambda a: a.xori(11, 6, 1)), words)
        self.assertIn(ppc.one(0, lambda a: a.stb(11, 4, 12)), words)

    def test_off_without_roster_restores_the_stock_dol(self):
        original = self.image.to_bytes()
        go.apply(self.image, ['cpu_vs_cpu', 'cpu_management'])
        log = go.remove(self.image, ['cpu_vs_cpu', 'cpu_management'])
        self.assertIn('sections are removed', log[-1])
        self.assertEqual(self.image.to_bytes(), original)
        self.assertIsNone(dhs.DolHammerspace.open(self.image))

    def test_off_without_roster_rebuilds_the_options_still_on(self):
        go.apply(self.image, ['cpu_vs_cpu'])
        only_cpu = self.image.to_bytes()
        go.apply(self.image, ['cpu_management'])
        go.remove(self.image, ['cpu_management'])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu'])
        self.assertEqual(self.image.to_bytes(), only_cpu)        # no unused stubs left behind

    def test_off_with_roster_data_keeps_the_sections(self):
        go.apply(self.image, ['cpu_vs_cpu'])
        hs = dhs.DolHammerspace.open(self.image)
        hs.data.put(b'\x01' * 0x20)                              # roster tables
        hs.commit()
        go.remove(self.image, ['cpu_vs_cpu'])
        self.assertEqual(go.detect(self.image), [])
        self.assertTrue(dhs.DolHammerspace.open(self.image).has_data)
        with self.assertRaisesRegex(dhs.HammerspaceError, 'roster data'):
            dhs.remove_sections(self.image)

    def test_apply_twice_is_a_no_op(self):
        go.apply(self.image, ['cpu_vs_cpu'])
        once = self.image.to_bytes()
        self.assertIn('already on', go.apply(self.image, ['cpu_vs_cpu'])[0])
        self.assertEqual(self.image.to_bytes(), once)

    def test_foreign_patch_is_refused(self):
        self.image.write_word(go.CPU_SITE, 0x12345678)
        with self.assertRaisesRegex(go.GameOptionError, 'another patch'):
            go.apply(self.image, ['cpu_vs_cpu'])

    def test_unknown_option(self):
        with self.assertRaisesRegex(go.GameOptionError, 'unknown game option'):
            go.apply(self.image, ['nope'])


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
            json.dump({'version': 1}, f)

    def image(self) -> dolfile.DolImage:
        with open(self.dol_path, 'rb') as f:
            return dolfile.DolImage(f.read())

    def test_on_off_and_dry_run(self):
        options_runner.run(self.tmp, on=['cpu_vs_cpu'], dry_run=True)
        self.assertEqual(go.detect(self.image()), [])
        log = options_runner.run(self.tmp, on=['cpu_vs_cpu'])
        self.assertEqual(go.detect(self.image()), ['cpu_vs_cpu'])
        self.assertIn('cpu_management: off', ' '.join(log))
        options_runner.run(self.tmp, off=['cpu_vs_cpu'])
        self.assertEqual(go.detect(self.image()), [])

    def test_refusals(self):
        with self.assertRaisesRegex(options_runner.GameOptionsRunError, 'both on and off'):
            options_runner.run(self.tmp, on=['cpu_vs_cpu'], off=['cpu_vs_cpu'])
        os.remove(self.dol_path)
        with self.assertRaisesRegex(options_runner.GameOptionsRunError, 'missing'):
            options_runner.run(self.tmp)

    def test_options_survive_roster_runs(self):
        options_runner.run(self.tmp, on=['cpu_vs_cpu', 'cpu_management'])
        roster_runner.run(self.tmp, config_path=self.config, input_dir=self.input_dir)
        self.assertEqual(go.detect(self.image()), ['cpu_vs_cpu', 'cpu_management'])
        roster_runner.run(self.tmp, remove_only=True, input_dir=self.input_dir)
        self.assertEqual(go.detect(self.image()), ['cpu_vs_cpu', 'cpu_management'])
        options_runner.run(self.tmp, off=['cpu_vs_cpu', 'cpu_management'])
        roster_runner.run(self.tmp, remove_only=True, input_dir=self.input_dir)
        with open(self.dol_path, 'rb') as f:
            self.assertEqual(f.read(), self.original)


if __name__ == '__main__':
    unittest.main()
