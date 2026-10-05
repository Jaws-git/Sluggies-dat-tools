"""The grid reader (``Roster/state.py``) and the roster manifest.

The real roster steps run on the synthetic inventory DOL (``test_roster_ids``
/ ``test_roster_grid``); ``read_state`` must give back exactly what the
config asked for.
"""

import struct
import unittest
from unittest import mock

from SluggiesTools.Dol import inventory, relocate
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import grid, ids, layout_file, manifest, names, runner, slots, state, steps, wheels
from SluggiesTools.tests.test_roster_grid import HEADS, STOCK_MAP, image_for_grid
from SluggiesTools.tests.test_roster_names import stock_table
from SluggiesTools.tests.test_roster_wheels import FakeLayout

BUILD_STEPS = ('dol_hammerspace', 'ids', 'wheels', 'grid')     # the steps that need no DAT


def build(config: dict):
    """The roster steps on the synthetic DOL (layout edits recorded, not applied), plus the manifest."""
    ctx = steps.RosterContext(dol=image_for_grid(), dat=None, config=config)
    ctx.state[layout_file.STATE_KEY] = FakeLayout()
    with mock.patch.object(relocate, 'scan_refs', return_value=[]):
        for step in steps.all_steps():
            if step.key in BUILD_STEPS:
                step.apply(ctx)
        runner.write_manifest(ctx)
    return ctx.dol, ctx


def rewrite_manifest(image, ctx) -> None:
    """Replace the run's manifest by one built from the (edited) ``ctx.state``."""
    hs = dhs.DolHammerspace.open(image)
    first = hs.data.blob.find(manifest.MAGIC)
    hs.data.blob[first:first + len(manifest.MAGIC)] = b'X' * len(manifest.MAGIC)
    hs.commit()
    ctx.state['hammerspace'] = hs
    runner.write_manifest(ctx)


def cells_of(result: dict) -> list:
    """Each cell as the head character of its square (None: empty)."""
    return [None if i is None else result['squares'][i]['head'] for i in result['cells']]


def members_of(result: dict, head: int) -> list[int]:
    return next(sq['members'] for sq in result['squares'] if sq['head'] == head)


SQUARES_12X5 = {
    'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None},
            {'id': '0x67', 'template': '0x06', 'wheel': None},
            {'id': '0x68', 'template': '0x00', 'wheel': '0x06'}],
    'wheels': [{'id': '0x47', 'wheel': '0x06'}],
    'grid': {'squares': [['0x66', '0x67'], ['0x47']], 'shape': [12, 5]},
}


class StockTests(unittest.TestCase):
    def test_untouched_dol(self):
        result = state.read_state(image_for_grid())
        self.assertEqual((result['kind'], result['shape'], result['luigi_own_square']), ('stock', [10, 4], False))
        self.assertEqual(cells_of(result), [HEADS[h] for h in STOCK_MAP])
        self.assertTrue(all(sq['kind'] == 'stock' for sq in result['squares']))
        self.assertEqual(members_of(result, 0x06), [0x06, 0x42, 0x43])   # the synthetic Yoshi wheel
        self.assertEqual(result['warnings'], [])
        self.assertFalse(result['names_read'])

    def test_stock_luigi_is_listed_off_grid(self):
        """The stock grid has no Luigi square; his family is an off-grid square so the GUI can reach it."""
        result = state.read_state(image_for_grid())
        [index] = result['off_grid']
        luigi = result['squares'][index]
        self.assertEqual((luigi['head'], luigi['off_grid'], luigi['members']), (0x01, True, [0x01]))
        self.assertNotIn(index, result['cells'])
        self.assertIn(0x01, [c['id'] for c in result['characters']])

    def test_roster_without_grid(self):
        image, _ctx = build({'ids': [{'id': '0x66', 'template': '0x06', 'wheel': '0x06'}]})
        result = state.read_state(image)
        self.assertEqual((result['kind'], result['shape']), ('expanded', [10, 4]))
        self.assertEqual(members_of(result, 0x06), [0x06, 0x42, 0x43, 0x66])
        new = next(c for c in result['characters'] if c['id'] == 0x66)
        self.assertEqual((new['template'], new['model_dir'], new['stats']), (0x06, 0x06 + 0x12, 0x06))


class ExpandedGridTests(unittest.TestCase):
    def test_stock_squares_at_eleven_by_four(self):
        image, ctx = build({'grid': {}})
        result = state.read_state(image)
        g = ctx.state['grid']
        self.assertEqual((result['shape'], result['luigi_own_square']), ([11, 4], True))
        self.assertEqual(cells_of(result), [None if c is None else HEADS[c[1]] for c in g.cells])
        self.assertEqual(result['cells'].count(None), 3)
        self.assertEqual(members_of(result, 0x01), [0x01])
        self.assertEqual(result['off_grid'], [])                           # Luigi has his own square here
        self.assertEqual(result['warnings'], [])

    def test_twelve_by_five_with_squares(self):
        image, ctx = build(SQUARES_12X5)
        result = state.read_state(image)
        g = ctx.state['grid']
        self.assertEqual(result['shape'], [12, 5])
        expected = [None if c is None else HEADS[c[1]] if c[0] == 'stock' else g.squares[c[1]][0] for c in g.cells]
        self.assertEqual(cells_of(result), expected)
        new = [sq for sq in result['squares'] if sq['kind'] == 'new']
        self.assertEqual([sq['members'] for sq in new], [[0x66, 0x67], [0x47]])
        self.assertEqual([sq['head_index'] for sq in new], [43, 44])
        self.assertEqual([sq['voice'] for sq in new], [0x06, 0x06])          # the head's species: 0x47 is a Yoshi
        # 0x47 left Yoshi's wheel for its square; 0x68 joined it
        self.assertEqual(members_of(result, 0x06), [0x06, 0x42, 0x43, 0x68])
        self.assertEqual({c['id']: c['square'] for c in result['characters']}[0x47],
                         result['cells'][g.cells.index(('square', 1))])
        self.assertEqual(result['warnings'], [])

    def test_wheel_order(self):
        config = {'ids': [{'id': '0x66', 'template': '0x06', 'wheel': '0x06'},
                          {'id': '0x67', 'template': '0x06', 'wheel': '0x06'}],
                  'wheel_order': [['0x67', '0x06']], 'grid': {}}
        result = state.read_state(build(config)[0])
        self.assertEqual(members_of(result, 0x06), [0x67, 0x06, 0x42, 0x43, 0x66])


class NameTests(unittest.TestCase):
    def test_read_names_from_the_three_tables(self):
        tables = {lang: names.names_table(stock_table(), {0x66: f'Purple {lang}'}) for lang in ('en', 'fr', 'sp')}
        blob, words = b'', [0] * 12
        for k, lang in enumerate(('en', 'fr', 'sp')):
            words[4 * k] = state.dhs.hh._DAT_FNAME_PTR
            words[4 * k + 1:4 * k + 4] = [len(tables[lang]), len(blob), len(tables[lang])]
            blob += tables[lang]

        class Image:
            def is_mapped(self, _address, _size):
                return True

            def read(self, _address, size):
                return struct.pack('>12I', *words)[:size]

        class Dat:
            def read(self, offset, size):
                return blob[offset:offset + size]
        with mock.patch.object(names, 'name_record', return_value=0x80001000):
            got = state.read_names(Image(), Dat())
        self.assertEqual(got[0x66], {'en': 'Purple en', 'fr': 'Purple fr', 'sp': 'Purple sp'})
        self.assertEqual(got[0x67]['en'], names.UNNAMED)
        self.assertEqual(len(got), names.LAST_ID + 1)
        self.assertIsNone(state.read_names(Image(), None))

    def test_names_reach_the_characters_and_are_checked(self):
        config = dict(SQUARES_12X5, ids=[dict(e, name='Purple') if e['id'] == '0x66' else e
                                         for e in SQUARES_12X5['ids']])
        image, ctx = build(config)
        self.assertEqual(manifest.find(bytes(dhs.DolHammerspace.open(image).data.blob))['names'], {})
        text = {0x66: {'en': 'Purple', 'fr': 'Purple', 'sp': 'Purple'}, 0x06: {'en': 'Yoshi'},
                0x47: {'en': '#N/A', 'fr': '#N/A', 'sp': '#N/A'}}
        ctx.state['names'] = {0x66: {'en': 'Lilac', 'fr': 'Lilac', 'sp': 'Lilac'}}
        rewrite_manifest(image, ctx)                            # a manifest that disagrees: warns
        with mock.patch.object(state, 'read_names', return_value=text):
            result = state.read_state(image, dat=object())
        by_id = {c['id']: c for c in result['characters']}
        self.assertEqual(state.display_name(by_id[0x66]), 'Purple')
        self.assertEqual(state.display_name(by_id[0x67]), '(unnamed 0x67)')
        self.assertEqual((by_id[0x47]['default_name'], state.display_name(by_id[0x47])), ('Black Yoshi',) * 2)
        self.assertIsNone(by_id[0x66]['default_name'])
        self.assertTrue(any('0x66' in w and 'Purple' in w for w in result['warnings']))


class ModelBlockTests(unittest.TestCase):
    """Each character's ``blocks``: its model directory's files 0/1 as routed, with a SHA-1 fingerprint."""

    def test_blocks_and_fingerprints(self):
        import hashlib
        from SluggiesTools.Roster import dat_hammerspace

        records = {}
        for directory, at, routes in ((24, 0x80600000, [(0x100, 4), (0x200, 2)]), (172, 0x807C0000, [(0x300, 4)])):
            for index, (offset, length) in enumerate(routes):
                records[at + 48 * index] = struct.pack('>12I', *([dat_hammerspace.hh._DAT_FNAME_PTR, length,
                                                                 offset, length] * 3))
        pointers = [0x80500000] * 24 + [0x80600000] + [0x80500000] * 147 + [0x807C0000]

        class Image:
            def is_mapped(self, address, size):
                return True

            def read(self, address, size):
                return records.get(address, bytes(size))

        class Dat:
            def read(self, offset, size):
                return bytes([offset >> 8]) * size

        characters = [{'id': 0x06, 'model_dir': 24}, {'id': 0x66, 'model_dir': 172}, {'id': 0x67, 'model_dir': 999}]
        with mock.patch.object(dat_hammerspace, 'dir_pointers', return_value=pointers):
            state._model_blocks(Image(), Dat(), characters, [])
        self.assertEqual(characters[0]['blocks'], {
            'high': {'offset': 0x100, 'length': 4, 'sha1': hashlib.sha1(b'' * 4).hexdigest()},
            'low': {'offset': 0x200, 'length': 2, 'sha1': hashlib.sha1(b'' * 2).hexdigest()}})
        self.assertEqual(list(characters[1]['blocks']), ['high'])        # file 1 is no record here
        self.assertIsNone(characters[2]['blocks'])
        state._model_blocks(Image(), None, characters, [])
        self.assertIsNone(characters[0]['blocks'])


class ManifestTests(unittest.TestCase):
    def test_round_trip(self):
        _image, ctx = build(SQUARES_12X5)
        built = manifest.build(ctx.state)
        blob = b'\0' * 8 + manifest.encode(built) + b'\0' * 3
        self.assertEqual(manifest.find(blob), built)
        self.assertEqual(built['grid']['squares'], [[0x66, 0x67], [0x47]])
        self.assertEqual(built['spares'], [0x47])
        self.assertIsNone(manifest.find(b'\0' * 64))

    def test_damaged_or_foreign_version(self):
        blob = manifest.encode({'version': 99})
        with self.assertRaisesRegex(manifest.ManifestError, 'version 99'):
            manifest.find(blob)
        with self.assertRaisesRegex(manifest.ManifestError, 'damaged'):
            manifest.find(manifest.MAGIC + struct.pack('>I', 4) + b'junk')

    def test_mismatched_manifest_warns_and_the_binary_wins(self):
        image, ctx = build(SQUARES_12X5)
        ctx.state['grid'] = grid.Grid(12, 5, tuple(reversed(ctx.state['grid'].cells)), ctx.state['grid'].squares)
        rewrite_manifest(image, ctx)
        result = state.read_state(image)
        self.assertTrue(any('cells differ' in w for w in result['warnings']))
        self.assertEqual(cells_of(result)[0], 0x66)             # the DOL's map, not the reversed manifest

    def test_unknown_dols_are_refused(self):
        image = image_for_grid()
        site = inventory.group(grid.GROUP)[0].address
        image.write_word(site, image.u32(site) ^ 1)
        with self.assertRaisesRegex(state.StateError, 'not a roster this tool built'):
            state.read_state(image)
        image = image_for_grid()
        hs = dhs.DolHammerspace.create(image)                   # roster sections with data but no manifest
        hs.data.put(b'\x01' * 0x20)
        hs.commit()
        with self.assertRaisesRegex(state.StateError, 'older version'):
            state.read_state(image)

    def test_game_option_stubs_alone_read_as_stock(self):
        image = image_for_grid()
        stock = state.read_state(image)
        hs = dhs.DolHammerspace.create(image)                   # what GameOptions.apply leaves: text stubs only
        hs.code.put(b'\x60\x00\x00\x00' * 4, 4)
        hs.commit()
        self.assertFalse(dhs.DolHammerspace.open(image).has_data)
        result = state.read_state(image)
        self.assertEqual((result['kind'], cells_of(result)), (stock['kind'], cells_of(stock)))
        self.assertEqual(slots._manifest(image), {})


class TextGridTests(unittest.TestCase):
    def test_rows(self):
        result = state.read_state(build({'grid': {}})[0])
        lines = state.text_grid(result)
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0].count('|'), 10)
        self.assertEqual(lines[1].split('|')[0].strip(), '-')             # cell 11: empty at 11x4


if __name__ == '__main__':
    unittest.main()
