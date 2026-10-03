"""Roster expansion Phase 5: the icon bank built by the roster step (synthetic stock bank, no wimgt)."""

import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Dol import relocate
from SluggiesTools.Icons.tests.test_update_icon_source_tables import make_plain_source_bank
from SluggiesTools.Roster import dat_hammerspace as dhs
from SluggiesTools.Roster import dol_hammerspace, icons, ids, ledger, steps
from SluggiesTools.tests.test_roster_ids import fresh_image

STOCK = make_plain_source_bank()[:icons.cib.STOCK_BANK_LENGTH]
PAYLOAD = bytes(icons.pages.CMPR_IMAGE_LENGTH)
sources = icons.sources


def entry(cid, like=0x00):
    return icons.IconEntry(cid, f'{cid:02x}_side.png', f'{cid:02x}_front.png', like)


def records(bank, field):
    offset = sources._signed_pointer(bank, field)
    return {struct.unpack_from('>H', r, 2)[0]: r for r in icons._table(bank, offset)}, offset


class BuildBankTests(unittest.TestCase):
    def setUp(self):
        self.entries = [entry(0x48), entry(0x49, 0x01), entry(0x66, 0x02)]
        self.bank = icons.build_bank(STOCK, self.entries, PAYLOAD, PAYLOAD)

    def test_rows_and_length(self):
        res = sources.RESOURCE_TABLE_OFFSET
        count, length = struct.unpack_from('>II', self.bank, res)
        self.assertEqual(count, sources.STOCK_RESOURCE_COUNT + 6)
        self.assertEqual(len(self.bank), res + length + icons.BANK_TAIL)
        end = struct.unpack_from('>I', self.bank, icons.pages.RELOCATED_ICON_TABLE + 0x10)[0]
        self.assertEqual(icons.pages.RELOCATED_ICON_TABLE + end, res + length)
        third_front = res + 8 + (count - 1) * 0x14                 # entry 2, front page, slot x 128
        page, _z, v1, u1, v2, u2 = struct.unpack_from('>HH4f', self.bank, third_front)
        self.assertEqual((page, v1, round(u1 * 1024)), (icons.pages.FRONT_PAGE, 0.0, 128))

    def test_keys(self):
        side, _ = records(self.bank, sources.SIDE_POINTER_FIELD)
        front, _ = records(self.bank, sources.FRONT_POINTER_FIELD)
        normal, _ = records(self.bank, sources.NORMAL_A_POINTER_FIELD)
        n = sources.STOCK_RESOURCE_COUNT
        self.assertEqual([struct.unpack_from('>H', side[c], 6)[0] for c in (0x48, 0x49, 0x66)], [n, n + 1, n + 2])
        self.assertEqual([struct.unpack_from('>H', front[c], 6)[0] for c in (0x48, 0x49, 0x66)], [n + 3, n + 4, n + 5])
        self.assertNotIn(0x48, normal)                               # spare rows: side and front only
        self.assertEqual(struct.unpack_from('>H', normal[0x66], 6)[0], n + 5)   # new IDs: normal_a = front row
        self.assertEqual(side[0x49][8:], side[0x01][8:])           # like 0x01: Luigi's record body
        self.assertEqual(side[0x66][8:], side[0x02][8:])

    def test_tables_back_to_back_sorted_and_last_frame(self):
        expect = sources.NORMAL_A_OFFSET
        for field in (sources.NORMAL_A_POINTER_FIELD, sources.SIDE_POINTER_FIELD, sources.FRONT_POINTER_FIELD):
            recs, offset = records(self.bank, field)
            self.assertEqual(offset, expect)
            table = icons._table(self.bank, offset)
            keys = [struct.unpack_from('>H', r, 2)[0] for r in table]
            self.assertEqual(keys, sorted(keys, reverse=True))
            self.assertEqual([r[0] & 1 for r in table], [0] + [1] * (len(table) - 1))
            self.assertGreaterEqual(struct.unpack_from('>H', self.bank, offset + 0x18)[0], 0x66)
            expect = offset + sources._source_table_info(self.bank, offset)[0]
        self.assertLessEqual(expect, icons.pages.SIDE_IMAGE_OFFSET)

    def test_existing_key_refused(self):
        with self.assertRaises(icons.IconBankError):
            icons.build_bank(STOCK, [entry(0x01)], PAYLOAD, PAYLOAD)


class ParseTests(unittest.TestCase):
    def parse(self, config):
        return icons.parse_icons(config, check_files=False)

    def test_defaults(self):
        config = {'ids': [{'id': '0x66', 'template': '0x06', 'icon': {'side': 'a.png', 'front': 'b.png'}}],
                  'wheels': [{'id': '0x49', 'icon': {'side': 'c.png', 'front': 'd.png'}}, {'id': '0x47'}]}
        out = self.parse(config)
        self.assertEqual([(e.char_id, e.like) for e in out], [(0x49, 0x01), (0x66, 0x06)])
        self.assertTrue(out[0].side_path.endswith('c.png'))
        self.assertEqual(self.parse({'ids': [], 'wheels': []}), [])

    def test_errors(self):
        icon = {'side': 'a.png', 'front': 'b.png'}
        for config, message in (({'ids': [{'template': 0, 'icon': icon}]}, 'explicit "id"'),
                                ({'wheels': [{'id': '0x47', 'icon': {'side': 'x/a.png', 'front': 'b.png'}}]}, 'plain'),
                                ({'wheels': [{'id': '0x47', 'icon': dict(icon, like='0x50')}]}, 'not a stock'),
                                ({'wheels': [{'id': '0x47', 'icon': dict(icon, fit='zoom')}]}, 'fit')):
            with self.assertRaisesRegex(icons.IconConfigError, message):
                self.parse(config)


class StepTests(unittest.TestCase):
    STOCK_AT = 0x1000

    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(dhs, 'BASE_SIZE', 0x100000))
        self.enterContext(mock.patch.object(icons.cib, 'STOCK_BANK_OFFSET', self.STOCK_AT))
        self.enterContext(mock.patch.object(icons, 'ICON_DIR', tmp))
        path = os.path.join(tmp, 'dt_na.dat')
        with open(path, 'wb') as f:
            f.write(bytes(self.STOCK_AT) + STOCK + bytes(0x100000 - self.STOCK_AT - len(STOCK)))
        for name in ('a.png', 'b.png'):
            open(os.path.join(tmp, name), 'wb').close()
        self.dat = ledger.DatFile(path)
        self.image = fresh_image()
        # stock words at the hook and resolver sites, the stock icon record, a minimal directory table
        for site, stock, _stub in icons.HOOKS:
            self.image.write_word(site, stock)
        self.image.write_word(icons.RESOLVER_SITE, icons.RESOLVER_STOCK)
        record = b''.join(struct.pack('>4I', dhs.hh._DAT_FNAME_PTR, len(STOCK), self.STOCK_AT, len(STOCK))
                          for _ in range(3))
        self.image.write(icons.ICON_RECORD, record)
        table = dhs.dol_base_address(dhs.hh._DIRS_START)
        self.image.write(table, struct.pack(f'>{dhs.hh._DIRS_COUNT}I', *[icons.ICON_RECORD] * dhs.hh._DIRS_COUNT))

    def run_step(self, config, state=None):
        ctx = steps.RosterContext(dol=self.image, dat=self.dat, config=config)
        ctx.state.update(state or {})
        return ctx, icons.apply(ctx, encode=lambda entries: (PAYLOAD, PAYLOAD))

    def test_spare_rows_and_hooks(self):
        hooked = icons.one(icons.HOOKS[0][0], lambda a: a.b(icons.HOOKS[0][2]))
        self.image.write_word(icons.HOOKS[0][0], hooked)          # the pipeline's lower hook
        icon = {'side': 'a.png', 'front': 'b.png'}
        _ctx, log = self.run_step({'wheels': [{'id': '0x47', 'icon': icon}]})
        words = dhs.read_record(self.image, icons.ICON_RECORD)
        offset, length, alloc = dhs.slot(words, 'fr')
        self.assertGreaterEqual(offset, 0x100000)
        self.assertEqual(alloc, length)
        bank = self.dat.read(offset, length)
        self.assertEqual(bank, icons.build_bank(STOCK, icons.parse_icons(
            {'wheels': [{'id': '0x47', 'icon': icon}]}, icons.ICON_DIR), PAYLOAD, PAYLOAD))
        self.assertEqual(self.image.u32(icons.HOOKS[0][0]), icons.HOOKS[0][1])
        self.assertIn('back to stock', ' '.join(log))
        self.assertEqual(self.image.u32(icons.RESOLVER_SITE), icons.RESOLVER_STOCK)   # no new IDs with art

    def test_new_id_with_art(self):
        hs = dol_hammerspace.DolHammerspace.create(self.image)
        state = {}
        config = {'ids': [{'id': '0x66', 'template': '0x00', 'icon': {'side': 'a.png', 'front': 'b.png'}}]}
        with mock.patch.object(relocate, 'scan_refs', return_value=[]):
            ids.apply_ids(self.image, hs, ids.parse_ids(config), state)
        hs.commit()
        state['hammerspace'] = hs
        self.assertEqual(self.image.read(state['portrait_of'] + 0x66, 1), b'\x00')
        _ctx, log = self.run_step(config, state)
        self.assertEqual(self.image.read(state['portrait_of'] + 0x66, 1), b'\x66')
        self.assertEqual(self.image.u32(icons.RESOLVER_SITE) >> 26, 18)
        self.assertIn('own portraits: 0x66', ' '.join(log))

    def test_nothing_configured(self):
        before = self.image.to_bytes()
        _ctx, log = self.run_step({'wheels': [{'id': '0x47'}]})
        self.assertEqual(self.image.to_bytes(), before)
        self.assertIn('stays as it is', log[0])


if __name__ == '__main__':
    unittest.main()
