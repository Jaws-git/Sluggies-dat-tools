"""GUI character grid, Phase 3: read -> rebuild (``Roster/derive.py``) gives byte-identical game files.

The synthetic inventory DOL (``test_roster_grid.image_for_grid``) gets an icon
record and a name record; the synthetic DAT holds the stock icon bank (with
a real C8 page 0, ``test_roster_state_icons``) and three stock name tables.
Every roster step except the layout copy runs for real (the select layout is
a recording stand-in). Then ``derive`` reads the result back and the same
steps rebuild from the derived config on fresh copies of the input files:
``main.dol`` and ``dt_na.dat`` must come out byte for byte the same.

The CMPR encoder is a stand-in that drifts on every decode -> encode (as
wimgt does on real art), so the round trip only holds because the icons step
keeps each unchanged portrait's encoded blocks.
"""

import json
import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from SluggiesTools.Dol import relocate
from SluggiesTools.Icons import gx_decode, layout2d
from SluggiesTools.Roster import dat_hammerspace as dhs
from SluggiesTools.Roster import datfile, derive, icon_art, icons, layout_file, manifest, names, runner, state, steps
from SluggiesTools.tests.test_roster_grid import image_for_grid
from SluggiesTools.tests.test_roster_names import plate_bank, stock_table
from SluggiesTools.tests.test_roster_state_icons import STOCK0
from SluggiesTools.tests.test_roster_voice_stats_reassign import fill_voice_tables

STOCK_AT = 0x1000
NAMES_AT = {'en': 0xA0000, 'fr': 0xA4000, 'sp': 0xA8000}
NAME_DIR_POINTER = 0x80692C28           # dir 121's record list (its file 5 is the name record)
BASE = 0x100000                         # stands in for BASE_SIZE


def drifting_cmpr(page) -> bytes:
    """A decodable CMPR encoding of ``page.image`` that is not stable: each 4x4 block gets its opaque pixels' mean
    colour with red one RGB565 step higher, so decode -> encode moves every opaque block."""
    pixels = np.asarray(page.image.convert('RGBA')).astype(int)
    out = bytearray(page.payload_length)
    for by in range(page.height // 4):
        for bx in range(page.width // 4):
            block = pixels[4 * by:4 * by + 4, 4 * bx:4 * bx + 4].reshape(16, 4)
            opaque = block[:, 3] >= 128
            if opaque.any():
                r, g, b = block[opaque, :3].mean(axis=0)
                colour = min(31, round(r * 31 / 255) + 1) << 11 | round(g * 63 / 255) << 5 | round(b * 31 / 255)
            else:
                colour = 0
            if opaque.all():                 # four-colour mode (c0 > c1), every texel colour 0
                c0, c1 = max(colour, 1), max(colour, 1) - 1
                bits = 0
            else:                            # three-colour mode (c0 <= c1): index 3 is transparent
                c0 = c1 = colour
                bits = sum((0 if o else 3) << (30 - 2 * i) for i, o in enumerate(opaque))
            o = icon_art._block_offset(page.width, bx, by)
            out[o:o + 8] = struct.pack('>HHI', c0, c1, bits)
    return bytes(out)


class RecordingLayout:
    """Stands in for ``layout_file.LayoutFiles``: a plate bank to read, every transform's output recorded."""

    def __init__(self):
        self.outputs = []

    def read(self, _lang):
        return plate_bank()

    def update(self, transform):
        for lang in dhs.LANGS:
            try:
                self.outputs.append(transform(lang, plate_bank()))
            except (layout2d.Layout2dError, KeyError, ValueError, IndexError, struct.error) as exc:
                self.outputs.append(type(exc).__name__)     # grid/popup edits need the real select layout
        return ['layout updated']


def portrait(colour, hole=False) -> Image.Image:
    img = Image.new('RGBA', (48, 51), colour)
    if hole:
        for x in range(10, 20):
            for y in range(5, 30):
                img.putpixel((x, y), (0, 0, 0, 0))
    return img


class Harness(unittest.TestCase):
    """Synthetic input files in a temp folder; ``build`` runs the roster steps on fresh copies of them."""

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(dhs, 'BASE_SIZE', BASE))
        self.enterContext(mock.patch.object(icons, 'STOCK_BANK_OFFSET', STOCK_AT))
        self.enterContext(mock.patch.object(icons, 'encode_page', drifting_cmpr))
        self.enterContext(mock.patch.object(names, 'PLATE_TEMPLATE_PAGE', 0))
        self.enterContext(mock.patch.object(relocate, 'scan_refs', return_value=[]))
        self.icon_dir = os.path.join(self.tmp, 'icons_in')
        os.makedirs(self.icon_dir)
        for name, img in (('red.png', portrait((170, 40, 40, 255), hole=True)), ('blue.png', portrait((40, 90, 200, 255))),
                          ('green.png', portrait((30, 180, 60, 255), hole=True)), ('grey.png', portrait('grey'))):
            img.save(os.path.join(self.icon_dir, name))
        dat = bytearray(BASE)
        dat[STOCK_AT:STOCK_AT + len(STOCK0)] = STOCK0
        table = stock_table()
        for at in NAMES_AT.values():
            dat[at:at + len(table)] = table
        self.input_dat = os.path.join(self.tmp, 'input.dat')
        with open(self.input_dat, 'wb') as f:
            f.write(dat)
        image = image_for_grid()
        fill_voice_tables(image)
        for site, stock, _stub in icons.OLD_HOOKS:
            image.write_word(site, stock)
        image.write_word(icons.RESOLVER_SITE, icons.RESOLVER_STOCK)
        image.write(icons.ICON_RECORD, b''.join(struct.pack('>4I', dhs.hh._DAT_FNAME_PTR, len(STOCK0), STOCK_AT,
                                                            len(STOCK0)) for _ in range(3)))
        pointers = [icons.ICON_RECORD] * dhs.hh._DIRS_COUNT
        pointers[names.NAME_DIR] = NAME_DIR_POINTER
        image.write(dhs.dol_base_address(dhs.hh._DIRS_START), struct.pack(f'>{len(pointers)}I', *pointers))
        image.write(NAME_DIR_POINTER + names.NAME_FILE * dhs.RECORD_SIZE, b''.join(
            struct.pack('>4I', dhs.hh._DAT_FNAME_PTR, len(table), NAMES_AT[lang], len(table)) for lang in dhs.LANGS))
        self.input_dol = image.to_bytes()
        self.runs = 0

    def build(self, config: dict, icon_dir: str | None = None):
        """The roster steps (all but the layout copy) on fresh copies of the input files, then the manifest.
        Returns (DOL bytes, DAT bytes, context, the DAT file, the DOL image)."""
        self.runs += 1
        path = os.path.join(self.tmp, f'run{self.runs}.dat')
        shutil.copyfile(self.input_dat, path)
        dat = datfile.DatFile(path)
        from SluggiesTools.Dol import dolfile
        image = dolfile.DolImage(self.input_dol)
        ctx = steps.RosterContext(dol=image, dat=dat, config=config,
                                  icon_dir=self.icon_dir if icon_dir is None else icon_dir)
        ctx.state[layout_file.STATE_KEY] = RecordingLayout()
        for step in steps.all_steps():
            if step.key != 'layout_file':
                step.apply(ctx)
        runner.write_manifest(ctx)
        dat.flush()
        with open(path, 'rb') as f:
            return image.to_bytes(), f.read(), ctx, dat, image

    def round_trip(self, config: dict):
        """Build, derive, rebuild twice; returns the derived config. Asserts byte identity each time."""
        dol1, dat1, ctx1, dat, image = self.build(config)
        derived = derive.derive(image, dat)
        self.assertEqual(derived.warnings, [])
        folder = os.path.join(self.tmp, f'derived{self.runs}')
        path = derive.write(derived, folder)
        with open(path, encoding='utf-8') as f:
            config2 = json.load(f)
        dol2, dat2, ctx2, dat_b, image_b = self.build(config2, derive.icon_dir_of(path))
        self.assertEqual(dol2, dol1, 'main.dol differs after read -> rebuild')
        self.assertEqual(dat2, dat1, 'dt_na.dat differs after read -> rebuild')
        self.assertEqual(ctx2.state[layout_file.STATE_KEY].outputs, ctx1.state[layout_file.STATE_KEY].outputs)
        again = derive.write(derive.derive(image_b, dat_b), os.path.join(self.tmp, f'derived{self.runs}b'))
        with open(again, encoding='utf-8') as f:
            config3 = json.load(f)
        self.assertEqual(config3, config2)
        dol3, dat3, *_ = self.build(config3, derive.icon_dir_of(again))
        self.assertEqual((dol3, dat3), (dol1, dat1), 'a second rebuild differs')
        return config2


ICON = {'side': 'red.png', 'front': 'blue.png'}
CONFIGS = {
    'stock roster': {'version': 1},
    'empty ids and wheels': {'ids': [], 'wheels': []},
    'stock squares at 11x4': {'grid': {}},
    'wheel order': {'ids': [{'id': '0x66', 'template': '0x06', 'wheel': '0x06'},
                            {'id': '0x67', 'template': '0x06', 'wheel': '0x06'}],
                    'wheel_order': [['0x67', '0x06']], 'grid': {}},
    '12x5 with squares': {
        'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None},
                {'id': '0x67', 'template': '0x06', 'wheel': None},
                {'id': '0x68', 'template': '0x00', 'wheel': '0x06'}],
        'wheels': [{'id': '0x47', 'wheel': '0x06'}],
        'grid': {'squares': [['0x66', '0x67'], ['0x47']], 'shape': [12, 5]}},
    'icons, names and a grid': {
        'ids': [{'id': '0x69', 'template': '0x02', 'icon': {'side': 'green.png', 'front': 'grey.png'},
                 'name': 'Green'},
                {'id': '0x66', 'template': '0x00', 'icon': ICON, 'name': {'en': 'Red', 'fr': 'Rouge', 'sp': 'Rojo'}},
                {'id': '0x67', 'template': '0x00', 'icon': dict(ICON, like='0x05')},
                {'id': '0x68', 'template': '0x06', 'wheel': None, 'name': 'Square'},
                {'id': '0x6A', 'template': '0x06', 'wheel': '0x06', 'swatch': 'green'}],
        'wheels': [{'id': '0x48', 'swatch': 'white', 'icon': {'side': 'red.png', 'front': 'green.png'},
                    'name': 'White'},
                   {'id': '0x4B', 'wheel': '0x06'}],
        'grid': {'squares': [['0x68']], 'shape': [11, 5]}},
    'stats and a square voice': {
        'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None, 'stats': '0x09'},
                {'id': '0x67', 'template': '0x00', 'wheel': None},
                {'id': '0x68', 'template': '0x00', 'wheel': '0x06', 'stats': '0x0D'}],
        'grid': {'squares': [{'members': ['0x66', '0x67', '0x68'], 'voice': '0x09'}], 'shape': [11, 5]}},
    'stock portraits only': {'stock_icons': [{'id': '0x02', 'icon': ICON}]},
    'stock stats and voices only': {'stock_stats': [{'id': '0x00', 'stats': '0x09'}],
                                    'stock_voices': [{'id': '0x0D', 'voice': '0x09'}]},
    'stock stats and voices with a voiced square': {
        'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None, 'stats': '0x0D'}],
        'stock_stats': [{'id': '0x00', 'stats': '0x09'}, {'id': '0x0D', 'stats': '0x00'}],
        'stock_voices': [{'id': '0x00', 'voice': '0x09'}, {'id': '0x09', 'voice': '0x00'}],
        'grid': {'squares': [{'members': ['0x66'], 'voice': '0x09'}], 'shape': [11, 5]}},
    'stock names only': {'stock_names': [{'id': '0x0D', 'name': 'Little Toad'}]},
    'stock names with new IDs': {
        'ids': [{'id': '0x66', 'template': '0x00', 'name': 'Red'}],
        'wheels': [{'id': '0x48', 'name': 'White'}],
        'stock_names': [{'id': '0x01', 'name': {'en': 'Luigi', 'fr': 'Louis', 'sp': 'Luis'}},
                        {'id': '0x0D', 'name': 'Little Toad'}]},
    'stock portraits with new IDs': {
        'ids': [{'id': '0x66', 'template': '0x00', 'icon': {'side': 'green.png', 'front': 'grey.png'}}],
        'wheels': [{'id': '0x48', 'icon': ICON}],
        'stock_icons': [{'id': '0x05', 'icon': {'side': 'grey.png', 'front': 'red.png'}},
                        {'id': '0x01', 'icon': ICON}]},
}


class RoundTripTests(Harness):
    def test_every_config_rebuilds_byte_identical(self):
        for label, config in CONFIGS.items():
            with self.subTest(label):
                self.round_trip(config)

    def test_derived_config(self):
        derived = self.round_trip(CONFIGS['icons, names and a grid'])
        self.assertEqual(set(derived) - {'version', 'comment'}, {'ids', 'wheels', 'grid'})
        by_id = {e['id']: e for e in derived['ids'] + derived['wheels']}
        # icon order is the bank's (config order: wheels first, then ids as listed)
        icon_order = [e['id'] for e in derived['wheels'] + derived['ids'] if 'icon' in e]
        self.assertEqual(icon_order, ['0x48', '0x69', '0x66', '0x67'])
        self.assertEqual(by_id['0x67']['icon']['like'], '0x05')            # a non-default like is recovered
        self.assertEqual(by_id['0x66']['icon']['like'], '0x00')
        self.assertEqual(by_id['0x66']['icon']['side'], by_id['0x67']['icon']['side'])   # one shared cell
        self.assertEqual(by_id['0x66']['name'], {'en': 'Red', 'fr': 'Rouge', 'sp': 'Rojo'})
        self.assertEqual(by_id['0x69']['name'], {'en': 'Green', 'fr': 'Green', 'sp': 'Green'})
        self.assertNotIn('name', by_id['0x6A'])                             # only configured names are carried
        self.assertEqual(by_id['0x6A']['swatch'], 3)
        self.assertEqual(by_id['0x48'], {'id': '0x48', 'swatch': 9, 'name': {'en': 'White', 'fr': 'White',
                                                                              'sp': 'White'},
                                          'icon': by_id['0x48']['icon']})
        self.assertEqual(derived['grid']['shape'], [11, 5])
        self.assertEqual(derived['grid']['squares'], [['0x68']])
        self.assertEqual(len([c for c in derived['grid']['order'] if c]), 42)   # every square, in reading order

    def test_stats_and_voice_are_carried(self):
        derived = self.round_trip(CONFIGS['stats and a square voice'])
        self.assertEqual({e['id']: e.get('stats') for e in derived['ids']}, {'0x66': '0x09', '0x67': None,
                                                                            '0x68': '0x0D'})
        self.assertEqual(derived['grid']['squares'], [{'members': ['0x66', '0x67', '0x68'], 'voice': '0x09'}])

    def test_stock_stats_and_voices_are_carried(self):
        """GUI character grid Phase 7: ``stock_stats`` and ``stock_voices`` come back from the manifest."""
        derived = self.round_trip(CONFIGS['stock stats and voices with a voiced square'])
        self.assertEqual(derived['stock_stats'], [{'id': '0x00', 'stats': '0x09'}, {'id': '0x0D', 'stats': '0x00'}])
        self.assertEqual(derived['stock_voices'], [{'id': '0x00', 'voice': '0x09'}, {'id': '0x09', 'voice': '0x00'}])
        self.assertEqual(derived['ids'][0]['stats'], '0x0D')
        self.assertEqual(derived['grid']['squares'], [{'members': ['0x66'], 'voice': '0x09'}])
        only = self.round_trip(CONFIGS['stock stats and voices only'])
        self.assertEqual(set(only) - {'version', 'comment'}, {'stock_stats', 'stock_voices'})

    def test_stock_portraits_are_carried(self):
        derived = self.round_trip(CONFIGS['stock portraits with new IDs'])
        self.assertEqual(set(derived) - {'version', 'comment'}, {'ids', 'wheels', 'stock_icons'})
        stock = derived['stock_icons']
        self.assertEqual([e['id'] for e in stock], ['0x05', '0x01'])          # bank order
        self.assertEqual(set(stock[0]['icon']), {'side', 'front', 'fit'})     # no like: own records
        self.assertEqual(stock[1]['icon']['side'], derived['wheels'][0]['icon']['side'])   # one shared cell
        only = self.round_trip(CONFIGS['stock portraits only'])
        self.assertEqual(set(only) - {'version', 'comment'}, {'stock_icons'})

    def test_stock_names_are_carried(self):
        """GUI character grid Phase 5: a renamed stock character becomes a ``stock_names`` entry (with the table's
        text), new IDs and spare rows keep their ``name``."""
        derived = self.round_trip(CONFIGS['stock names with new IDs'])
        self.assertEqual(derived['stock_names'],
                         [{'id': '0x01', 'name': {'en': 'Luigi', 'fr': 'Louis', 'sp': 'Luis'}},
                          {'id': '0x0D', 'name': {'en': 'Little Toad', 'fr': 'Little Toad', 'sp': 'Little Toad'}}])
        self.assertEqual(derived['ids'][0]['name']['en'], 'Red')
        self.assertEqual(derived['wheels'][0]['name']['en'], 'White')
        only = self.round_trip(CONFIGS['stock names only'])
        self.assertEqual(set(only) - {'version', 'comment'}, {'stock_names'})

    def test_a_stock_name_is_dropped_by_leaving_it_out(self):
        """Rebuilding without the entry restores the stock text (the reset of a blank rename)."""
        derived = self.round_trip(CONFIGS['stock names with new IDs'])
        derived['stock_names'] = [e for e in derived['stock_names'] if e['id'] != '0x0D']
        _dol, _dat, _ctx, dat, image = self.build(derived, self.icon_dir)
        again = derive.derive(image, dat).config
        self.assertEqual([e['id'] for e in again['stock_names']], ['0x01'])
        self.assertEqual(state.read_names(image, dat)[0x0D]['en'], 'Name 13')

    def test_new_id_wheel_host_comes_first(self):
        """Icon entries come first (bank order), but a new-ID wheel host still precedes its members."""
        config = {'ids': [{'id': '0x66', 'template': '0x06'},
                          {'id': '0x67', 'template': '0x06', 'wheel': '0x66', 'icon': ICON}]}
        derived = self.round_trip(config)
        self.assertEqual([e['id'] for e in derived['ids']], ['0x66', '0x67'])

    def test_a_replaced_portrait_is_encoded_afresh(self):
        config = {'ids': [{'id': '0x66', 'template': '0x00', 'icon': ICON}]}
        dol1, dat1, ctx, dat, image = self.build(config)
        path = derive.write(derive.derive(image, dat), os.path.join(self.tmp, 'derived'))
        with open(path, encoding='utf-8') as f:
            derived = json.load(f)
        side = os.path.join(derive.icon_dir_of(path), derived['ids'][0]['icon']['side'])
        portrait('yellow').save(side)                           # the .cmpr beside it no longer matches
        dol2, dat2, ctx2, dat_b, image_b = self.build(derived, derive.icon_dir_of(path))
        self.assertTrue(dat2 != dat1)
        bank = derive.state_icons.read_bank(image_b, dat_b)
        page_id, (x, y, _w, _h) = bank.row_rect(icons.STOCK_ROWS)
        decoded = bank.decode_page(page_id)[y:y + 51, x:x + 48]
        self.assertEqual(tuple(decoded[0, 0]), tuple(gx_decode.decode(
            gx_decode.CMPR, drifting_cmpr(icons.pack_page([portrait('yellow')])), 64, 64)[0, 0]))


class KeptBlockTests(unittest.TestCase):
    def test_cell_blocks_round_trip(self):
        page = icons.pack_page([portrait('red', hole=True), portrait('blue')])
        payload = drifting_cmpr(page)
        for x, y in page.cells:
            blocks = icon_art.cell_blocks(payload, page.width, x, y, icons.CELL)
            self.assertEqual(len(blocks), 13 * 13 * 8)
            cell = gx_decode.decode(gx_decode.CMPR, icon_art.cell_payload(blocks, icons.CELL), icons.CELL, icons.CELL)
            whole = gx_decode.decode(gx_decode.CMPR, payload, page.width, page.height)
            np.testing.assert_array_equal(cell, whole[y:y + icons.CELL, x:x + icons.CELL])
            out = bytearray(len(payload))
            icon_art.put_cell_blocks(out, page.width, x, y, icons.CELL, blocks)
            self.assertEqual(icon_art.cell_blocks(bytes(out), page.width, x, y, icons.CELL), blocks)

    def test_keep_blocks_only_when_they_match(self):
        page = icons.pack_page([portrait((120, 60, 30, 255))])
        first = drifting_cmpr(page)
        blocks = icon_art.cell_blocks(first, page.width, 0, 0, icons.CELL)
        decoded = gx_decode.decode(gx_decode.CMPR, first, page.width, page.height)
        art = Image.fromarray(decoded[:51, :48], 'RGBA')
        again = icons.pack_page([art], [('kept', 'a')], [blocks])
        reencoded = drifting_cmpr(again)
        self.assertTrue(reencoded != first)                     # the stand-in drifts
        kept, count = icons.keep_blocks(again, reencoded)
        self.assertTrue(kept == first)
        self.assertEqual(count, 1)
        other = icons.pack_page([portrait('blue')], [('kept', 'a')], [blocks])
        self.assertEqual(icons.keep_blocks(other, drifting_cmpr(other))[1], 0)

    def test_kept_files_stay_separate_cells(self):
        same = portrait('red')
        page = icons.pack_page([same, same, same], [('kept', 'a'), ('kept', 'b'), ('kept', 'a')])
        self.assertEqual(len(set(page.cells)), 2)
        self.assertEqual(page.cells[0], page.cells[2])


class ManifestTests(unittest.TestCase):
    def test_config_keys_and_spare_wheels(self):
        built = manifest.build({'spare_wheels': {0x47: (6, None), 0x48: (None, 9)}}, {'ids': [], 'wheels': []})
        self.assertEqual(built['config_keys'], ['ids', 'wheels'])
        self.assertEqual(built['spare_wheels'], [[0x47, 6, None], [0x48, None, 9]])

    def test_older_manifests_infer_the_keys(self):
        image = image_for_grid()
        self.assertEqual(derive.config_keys(image, {'spares': [0x47], 'grid': None, 'wheel_order': []}), ['wheels'])
        self.assertEqual(derive.config_keys(image, {'config_keys': ['ids']}), ['ids'])


if __name__ == '__main__':
    unittest.main()
