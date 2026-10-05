"""Portraits resolved the game's way (``Roster/state_icons.py``) and their crops.

A synthetic stock bank (``test_roster_icons.stock_bank``) gets a real C8 page
0 (one colour per 52-px cell, every resource row on it); the real icons step
then packs portraits for a spare row and a new ID into solid-colour CMPR
pages. ``state_icons`` must resolve each ID as the renderer does and cut the
crops the decoder gives.
"""

import os
import struct
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from SluggiesTools.Dol import relocate
from SluggiesTools.Roster import dat_hammerspace as dhs
from SluggiesTools.Roster import datfile, dol_hammerspace, icons, ids, state, state_icons, steps
from SluggiesTools.tests.test_roster_icons import STOCK
from SluggiesTools.tests.test_roster_ids import fresh_image

PAGE0 = (512, 64)
PAGE0_IMAGE, PAGE0_PALETTE = 0x2000, 0x2000 + PAGE0[0] * PAGE0[1]      # offsets from bank +0x20
CELLS = PAGE0[0] // 52                                                 # 9 cells of 52 px in one row


def cell_colour(k: int) -> tuple[int, int, int, int]:
    """RGB5A3 opaque colour of page 0's cell ``k`` (palette entry k + 1), as 8-bit RGBA."""
    r, g, b = (k * 3) % 32, (k * 7 + 5) % 32, (31 - k) % 32
    expand = lambda v: (v * 510 + 31) // 62
    return expand(r), expand(g), expand(b), 255


def rgb5a3(k: int) -> int:
    r, g, b = (k * 3) % 32, (k * 7 + 5) % 32, (31 - k) % 32
    return 0x8000 | r << 10 | g << 5 | b


def c8_tiles(index) -> bytes:
    """A (h, w) index array in C8's 8x4 tile order."""
    h, w = index.shape
    return index.reshape(h // 4, 4, w // 8, 8).swapaxes(1, 2).astype(np.uint8).tobytes()


def stock_with_page0() -> bytes:
    """``STOCK`` with page 0 a C8 RGB5A3 512x64 page (cell k filled with index k + 1) and resource row r on cell
    r % 9 of page 0."""
    bank = bytearray(STOCK)
    index = np.zeros((PAGE0[1], PAGE0[0]), np.uint8)
    for k in range(CELLS):
        index[0:52, k * 52:k * 52 + 52] = k + 1
    at = icons.TEX_BASE + PAGE0_IMAGE
    bank[at:at + index.size] = c8_tiles(index)
    palette = b''.join(struct.pack('>H', rgb5a3(k - 1) if k else 0) for k in range(256))
    at = icons.TEX_BASE + PAGE0_PALETTE
    bank[at:at + len(palette)] = palette
    d = icons.DESCRIPTOR_TABLE
    struct.pack_into('>IIHH', bank, d, PAGE0_IMAGE, PAGE0_PALETTE, PAGE0[1], PAGE0[0])
    bank[d + 0x17] = 0x09
    struct.pack_into('>H', bank, d + 0x18, 256)
    bank[d + 0x1A] = 2
    desc = icons.STOCK_CONTAINER + icons.CONTAINER_HEAD
    res = icons._pointer(bank, desc, icons.RESOURCE_FIELD)
    for r in range(icons.STOCK_ROWS):
        x = (r % CELLS) * 52
        struct.pack_into('>HHffff', bank, res + 8 + r * icons.ROW_SIZE, 0, 0, 0.0, x / PAGE0[0],
                         51 / PAGE0[1], (x + 48) / PAGE0[0])
    return bytes(bank)


STOCK0 = stock_with_page0()
SIDE_RGB, FRONT_RGB = 0x07E0, 0x001F                   # RGB565 green, blue


def solid_payload(colour: int):
    return lambda page: struct.pack('>HHI', colour, colour, 0) * (page.payload_length // 8)


class Fixture(unittest.TestCase):
    """A DOL + DAT with the stock bank (page 0 added) at 0x1000, optionally after the real ids/icons steps."""
    STOCK_AT = 0x1000
    CONFIG = {'ids': [{'id': '0x66', 'template': '0x00', 'icon': {'side': 'a.png', 'front': 'b.png'}},
                      {'id': '0x67', 'template': '0x02'}],
              'wheels': [{'id': '0x48', 'icon': {'side': 'a.png', 'front': 'b.png'}}]}

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(dhs, 'BASE_SIZE', 0x100000))
        self.enterContext(mock.patch.object(icons, 'STOCK_BANK_OFFSET', self.STOCK_AT))
        self.enterContext(mock.patch.object(icons, 'ICON_DIR', self.tmp))
        path = os.path.join(self.tmp, 'dt_na.dat')
        with open(path, 'wb') as f:
            f.write(bytes(self.STOCK_AT) + STOCK0 + bytes(0x100000 - self.STOCK_AT - len(STOCK0)))
        for name in ('a.png', 'b.png'):
            Image.new('RGBA', (48, 51), 'red').save(os.path.join(self.tmp, name))
        self.dat = datfile.DatFile(path)
        self.image = fresh_image()
        for site, stock, _stub in icons.OLD_HOOKS:
            self.image.write_word(site, stock)
        self.image.write_word(icons.RESOLVER_SITE, icons.RESOLVER_STOCK)
        record = b''.join(struct.pack('>4I', dhs.hh._DAT_FNAME_PTR, len(STOCK0), self.STOCK_AT, len(STOCK0))
                          for _ in range(3))
        self.image.write(icons.ICON_RECORD, record)
        table = dhs.dol_base_address(dhs.hh._DIRS_START)
        self.image.write(table, struct.pack(f'>{dhs.hh._DIRS_COUNT}I', *[icons.ICON_RECORD] * dhs.hh._DIRS_COUNT))
        self.portrait_of = None

    def build(self, config=None):
        """The real ids and icons steps; sets ``self.portrait_of``."""
        config = self.CONFIG if config is None else config
        hs = dol_hammerspace.DolHammerspace.create(self.image)
        st = {}
        with mock.patch.object(relocate, 'scan_refs', return_value=[]):
            ids.apply_ids(self.image, hs, ids.parse_ids(config), st)
        hs.commit()
        st['hammerspace'] = hs
        ctx = steps.RosterContext(dol=self.image, dat=self.dat, config=config)
        ctx.state.update(st)
        side_payload, front_payload = solid_payload(SIDE_RGB), solid_payload(FRONT_RGB)
        calls = []

        def encode(page):
            calls.append(page)
            return (side_payload if len(calls) == 1 else front_payload)(page)
        icons.apply(ctx, encode_cmpr=encode)
        self.portrait_of = st['portrait_of']

    def resolve(self, *cids):
        bank = state_icons.read_bank(self.image, self.dat)
        results, problems = state_icons.resolve_all(self.image, bank, cids, self.portrait_of)
        self.assertEqual(problems, [])
        return results


class StockResolveTests(Fixture):
    def test_own_key(self):
        r = self.resolve(0x00)[0x00]
        self.assertEqual((r['front']['source'], r['front']['key'], r['front']['row']), ('own', 0x00, 0x4F))
        self.assertEqual((r['side']['source'], r['side']['row']), ('own', 0x00))
        self.assertEqual(r['front']['page'], 0)
        self.assertEqual(r['front']['rect'], [(0x4F % CELLS) * 52, 0, 48, 51])

    def test_spare_row_without_key_shows_the_lower_neighbour(self):
        r = self.resolve(0x47)[0x47]
        for view in state_icons.VIEWS:
            self.assertEqual((r[view]['source'], r[view]['key']), ('neighbour', 0x46))

    def test_mii_and_invalid(self):
        r = self.resolve(0x50, 0x65)
        self.assertEqual((r[0x50]['front']['source'], r[0x50]['front']['row']), ('mii', state_icons.MII_ROW))
        self.assertEqual((r[0x65]['front']['source'], r[0x65]['front']['key']), ('invalid', 0x4D))
        self.assertEqual(r[0x65]['side']['key'], 0x46)          # the side table has no 0x4D key

    def test_every_stock_id_resolves(self):
        results = self.resolve(*range(ids.STOCK_IDS))
        self.assertEqual(len(results), ids.STOCK_IDS)


class RosterResolveTests(Fixture):
    def setUp(self):
        super().setUp()
        self.build()

    def test_spare_row_with_art(self):
        r = self.resolve(0x48)[0x48]
        self.assertEqual((r['side']['source'], r['side']['page']), ('own', icons.SIDE_PAGE))
        self.assertEqual((r['front']['source'], r['front']['page']), ('own', icons.FRONT_PAGE))

    def test_new_id_with_art(self):
        r = self.resolve(0x66)[0x66]
        self.assertTrue(state_icons.resolver_branch(self.image))
        self.assertEqual((r['front']['source'], r['front']['alias'], r['front']['key']), ('own', 0x66, 0x66))
        self.assertEqual(r['front']['page'], icons.FRONT_PAGE)

    def test_new_id_without_art_takes_its_template(self):
        r = self.resolve(0x67)[0x67]
        for view in state_icons.VIEWS:
            self.assertEqual((r[view]['source'], r[view]['alias'], r[view]['key']), ('template', 0x02, 0x02))

    def test_without_the_resolver_branch_a_new_id_is_invalid(self):
        self.image.write_word(icons.RESOLVER_SITE, icons.RESOLVER_STOCK)
        r = self.resolve(0x66)[0x66]
        self.assertEqual((r['front']['source'], r['front']['key']), ('invalid', 0x4D))

    def test_every_roster_id_resolves(self):
        cids = [*range(ids.STOCK_IDS), 0x66, 0x67]
        self.assertEqual(set(self.resolve(*cids)), set(cids))


class CropTests(Fixture):
    def setUp(self):
        super().setUp()
        self.build()
        self.out = os.path.join(self.tmp, 'icons')

    def crops(self, *cids):
        bank = state_icons.read_bank(self.image, self.dat)
        results, _ = state_icons.resolve_all(self.image, bank, cids, self.portrait_of)
        refs = [results[c][v] for c in cids for v in state_icons.VIEWS]
        counts = state_icons.write_crops(bank, refs, self.out)
        return results, counts

    def pixels(self, ref):
        with Image.open(os.path.join(self.out, ref['file'])) as png:
            return np.asarray(png.convert('RGBA'))

    def test_crop_pixels(self):
        results, (pages, written) = self.crops(0x00, 0x66)
        self.assertEqual((pages, written), (3, 4))              # page 0, the side and the front CMPR page
        stock = self.pixels(results[0x00]['front'])
        self.assertEqual(stock.shape, (51, 48, 4))
        self.assertEqual(np.unique(stock.reshape(-1, 4), axis=0).tolist(), [list(cell_colour(0x4F % CELLS))])
        self.assertTrue((self.pixels(results[0x66]['side']) == (0, 255, 0, 255)).all())
        self.assertTrue((self.pixels(results[0x66]['front']) == (0, 0, 255, 255)).all())

    def test_cache_hit_decodes_nothing_and_stale_crops_go(self):
        first, _ = self.crops(0x00, 0x66)
        stale = os.path.join(self.out, 'stale.png')
        Image.new('RGBA', (1, 1)).save(stale)
        again, counts = self.crops(0x00, 0x66)
        self.assertEqual(counts, (0, 0))
        self.assertFalse(os.path.exists(stale))
        self.assertEqual([r['file'] for r in again[0x00].values()], [r['file'] for r in first[0x00].values()])

    def test_cache_key_follows_the_page_bytes(self):
        bank = state_icons.read_bank(self.image, self.dat)
        before = bank.page_sha1(0)
        self.assertEqual(state_icons.read_bank(self.image, self.dat).page_sha1(0), before)
        offset = dhs.slot(icons.read_record(self.image), 'en')[0]
        self.dat.write(offset + icons.TEX_BASE + PAGE0_PALETTE + 2, b'\x80\x00')
        self.assertNotEqual(state_icons.read_bank(self.image, self.dat).page_sha1(0), before)


class ReadStateTests(Fixture):
    def test_without_a_dat_there_are_no_icons(self):
        result = state.read_state(self.image, None)
        self.assertFalse(result['icons_read'])
        self.assertTrue(all(c['icon'] is None for c in result['characters']))


if __name__ == '__main__':
    unittest.main()
