"""GUI character grid, Phase 4d: portraits from a model folder and stock portrait overrides (``stock_icons``)."""

import os
import struct
import tempfile
import unittest

from SluggiesTools.Icons import export_icons
from SluggiesTools.Roster import icons, model_icons, state_icons
from SluggiesTools.tests import test_roster_icons
from SluggiesTools.tests.test_roster_icons import STOCK, container, entry, packed, portrait


def model_tree(root: str, views=('side', 'front'), low=True) -> tuple[str, str]:
    """``<root>/32 Toad/`` with an HP folder (portraits for ``views``) and its ``L_`` partner; returns both."""
    char = os.path.join(root, '2_Output_Models', '32 Toad')
    hp = os.path.join(char, '79000000_toad.gpl')
    low_dir = os.path.join(char, '79400000_L_toad.gpl')
    for folder in (hp, low_dir) if low else (hp,):
        os.makedirs(folder)
        with open(os.path.join(folder, os.path.basename(folder) + '.sluggie'), 'w') as f:
            f.write('{}')
    if views:
        os.makedirs(os.path.join(hp, 'icon'))
    for view, colour in (('side', 'red'), ('front', 'blue')):
        if view in views:
            portrait(colour).save(os.path.join(hp, 'icon', model_icons.FILES[view]))
    return hp, low_dir


class FindTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())

    def test_names_match_the_icon_export(self):
        self.assertEqual(model_icons.FILES, export_icons.CHARACTER_ICON_FILES)
        self.assertEqual(model_icons.ICON_SUBDIR, export_icons.CHARACTER_ICON_DIR)

    def test_high_poly_folder_and_sluggie(self):
        hp, _low = model_tree(self.root)
        for path in (hp, os.path.join(hp, '79000000_toad.gpl.sluggie')):
            found = model_icons.find(path)
            self.assertTrue(found.ok, found.problem)
            self.assertEqual(found.home, hp)
            self.assertEqual(found.side, os.path.join(hp, 'icon', 'SideIcon.png'))
            self.assertEqual(found.front, os.path.join(hp, 'icon', 'FrontIcon.png'))

    def test_low_poly_uses_its_partner(self):
        hp, low = model_tree(self.root)
        found = model_icons.find(os.path.join(low, '79400000_L_toad.gpl.sluggie'))
        self.assertTrue(found.ok, found.problem)
        self.assertEqual(found.home, hp)

    def test_low_poly_without_partner(self):
        hp, low = model_tree(self.root)
        os.remove(os.path.join(hp, '79000000_toad.gpl.sluggie'))         # a folder without .sluggie is no partner
        found = model_icons.find(low)
        self.assertFalse(found.ok)
        self.assertIn('no high-poly partner', found.problem)

    def test_a_missing_view_refuses(self):
        hp, _low = model_tree(self.root, views=('front',))
        found = model_icons.find(hp)
        self.assertFalse(found.ok)
        self.assertIn('icon/SideIcon.png missing', found.problem)
        self.assertIsNone(found.side)
        none = model_icons.find(model_tree(os.path.join(self.root, 'b'), views=(), low=False)[0])
        self.assertIn('SideIcon.png and icon/FrontIcon.png missing', none.problem)

    def test_not_a_model_folder(self):
        self.assertIn('not found', model_icons.find(os.path.join(self.root, 'nothing')).problem)
        self.assertIn('not a model folder', model_icons.find(self.root).problem)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.hp, self.low = model_tree(self.root)

    def test_model_icon(self):
        config = {'ids': [{'id': '0x66', 'template': '0x0E', 'icon': {'model': self.low}}],
                  'stock_icons': [{'id': '0x0E', 'icon': {'model': self.hp, 'fit': 'strict'}}]}
        out = icons.parse_icons(config)
        self.assertEqual([(e.char_id, e.like, e.stock) for e in out], [(0x66, 0x0E, False), (0x0E, 0x0E, True)])
        self.assertEqual(out[0].side_path, os.path.join(self.hp, 'icon', 'SideIcon.png'))
        self.assertEqual(out[1].fit, 'strict')

    def test_a_missing_view_is_an_error(self):
        os.remove(os.path.join(self.hp, 'icon', 'FrontIcon.png'))
        with self.assertRaisesRegex(icons.IconConfigError, 'FrontIcon.png missing'):
            icons.parse_icons({'stock_icons': [{'id': '0x0E', 'icon': {'model': self.hp}}]})

    def test_errors(self):
        icon = {'side': 'a.png', 'front': 'b.png'}
        for config, message in (
                ({'stock_icons': [{'id': '0x47', 'icon': icon}]}, 'spare row'),
                ({'stock_icons': [{'id': '0x4D', 'icon': icon}]}, 'not a stock character'),
                ({'stock_icons': [{'id': '0x66', 'icon': icon}]}, 'not a stock character'),
                ({'stock_icons': [{'id': '0x02', 'icon': dict(icon, like='0x01')}]}, 'own records'),
                ({'stock_icons': [{'id': '0x02'}]}, 'needs an "id" and an "icon"'),
                ({'stock_icons': {'id': '0x02'}}, 'must be a list'),
                ({'stock_icons': [{'id': '0x02', 'icon': dict(icon, model=self.hp)}]}, 'not both'),
                ({'stock_icons': [{'id': '0x02', 'icon': icon}, {'id': '0x02', 'icon': icon}]}, 'two icon entries')):
            with self.subTest(message), self.assertRaisesRegex(icons.IconConfigError, message):
                icons.parse_icons(config, check_files=False)


def rows_of(bank: bytes) -> dict:
    """{field: {key: (resource row, record body)}} of the bank's three source tables."""
    _tex_end, _desc, ptr = container(bank)
    return {f: {struct.unpack_from('>H', r, 2)[0]: (struct.unpack_from('>H', r, 6)[0], bytes(r[8:]))
                for r in icons._table(bank, ptr(f))} for f in icons.TABLE_FIELDS}


class StockOverrideBankTests(unittest.TestCase):
    def test_own_keys_are_repointed(self):
        entries = [entry(0x48), entry(0x66, 0x02), entry(0x01, 0x01)]
        bank, _side, _front = packed(entries)
        before, after = rows_of(STOCK), rows_of(bank)
        n = icons.STOCK_ROWS
        side_row, front_row = n + 2, n + 3 + 2
        self.assertEqual(after[icons.SIDE_FIELD][0x01][0], side_row)
        self.assertEqual(after[icons.FRONT_FIELD][0x01][0], front_row)
        self.assertEqual(after[icons.NORMAL_A_FIELD][0x01][0], side_row)    # normal_a: the side view
        for f in icons.TABLE_FIELDS:
            self.assertEqual(after[f][0x01][1], before[f][0x01][1])          # the record's own data is kept
            self.assertEqual(len(after[f]), len(before[f]) + (1 if f == icons.NORMAL_A_FIELD else 2))
            for key in before[f]:
                if key != 0x01:
                    self.assertEqual(after[f][key], before[f][key])

    def test_resolves_as_own(self):
        bank, _side, _front = packed([entry(0x03, 0x03)])
        parsed = state_icons.IconBank(bank)
        for view, row in (('side', icons.STOCK_ROWS), ('front', icons.STOCK_ROWS + 1)):
            self.assertEqual(parsed.key_in_force(view, 0x03), (0x03, row))
            self.assertEqual(parsed.row_rect(row)[0], icons.SIDE_PAGE if view == 'side' else icons.FRONT_PAGE)

    def test_a_key_must_exist(self):
        with self.assertRaisesRegex(icons.IconBankError, 'no own key'):
            icons.extend_table(STOCK, icons._pointer(STOCK, icons.STOCK_CONTAINER + 0x14, icons.SIDE_FIELD),
                               {}, {0x47: 200})


class StockStepTests(test_roster_icons.StepTests):
    def test_stock_override_from_a_model_folder(self):
        hp, _low = model_tree(self.enterContext(tempfile.TemporaryDirectory()))
        config = {'stock_icons': [{'id': '0x02', 'icon': {'model': hp}}]}
        _ctx, log = self.run_step(config)
        self.assertIn('stock portraits replaced: 0x02', ' '.join(log))
        self.assertEqual(self.image.u32(icons.RESOLVER_SITE), icons.RESOLVER_STOCK)   # no new IDs with art
        words = icons.dhs.read_record(self.image, icons.ICON_RECORD)
        offset, length, _alloc = icons.dhs.slot(words, 'en')
        found = rows_of(self.dat.read(offset, length))
        self.assertEqual(found[icons.SIDE_FIELD][0x02][0], icons.STOCK_ROWS)
        self.assertEqual(found[icons.FRONT_FIELD][0x02][0], icons.STOCK_ROWS + 1)

    # the inherited tests run here too; they need nothing new
    test_spare_rows_and_hooks = test_new_id_with_art = test_nothing_configured = None


if __name__ == '__main__':
    unittest.main()
