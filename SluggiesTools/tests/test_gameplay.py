"""Gameplay settings (``Gameplay/``): field table, read, apply, settings files, AI Gameplay Tool files, carry.

A hand-built DOL maps D4 and D7 at their stock addresses and holds the stock
bytes of every gameplay table; nothing else.
"""

import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools import gecko_compat, gui_grid
from SluggiesTools.Dol import dolfile, relocate
from SluggiesTools.Gameplay import apply, carry, cli, legacy, read, tables, view
from SluggiesTools.Roster import runner, slot_plan
from SluggiesTools.StatEditor import carry as stat_carry
from SluggiesTools.StatEditor import fields
from SluggiesTools.tests.test_roster_voice_stats_reassign import NAMES, STATE_FILE, make_config, make_state
from SluggiesTools.tests.test_stat_editor_apply import StatEnv
from SluggiesTools.tests.test_stat_editor_carry import EXPANDED
from SluggiesTools.tests.test_stat_editor_carry import vanilla as roster_vanilla

D4, D4_SIZE = 0x806233C0, 0x16940
D7, D7_SIZE = 0x80796DC0, 0xE020

STOCK_USAGE = ((5, 5, 20, 75, 95), (15, 15, 30, 80, 95), (1, 1, 10, 30, 60), (25, 25, 30, 50, 95))
STOCK_COSTS = (0, 100, 100, 0, 50, 50)
STOCK_CHARGE = ((40, 10), (90, 40), (30, 10), (40, 10))
STOCK_MODIFIERS = ((0, 0), (0, 0), (-10, 0), (-20, 5))
STOCK_PITCH = ((0, 1), (1, 1), (0, 0), (0, 2))
STOCK_CHANCE = ((70, 70), (85, 85), (85, 80), (50, 60))
STOCK_STAMINA_ROW = (100, 90, 70, -10, -10, -2, -1, -3)
STOCK_BOOSTS = ((1, 1.0, 0, 10), (2, 1.1, 100, 200), (2, 1.3, 100, 200), (2, 1.5, 0, 150), (2, 1.5, 0, 150),
                (1, 1.0, 0, 10), (2, 1.1, 0, 150), (2, 1.3, 0, 150), (2, 1.5, 0, 150), (2, 1.5, 0, 150),
                (2, 1.5, 0, 150), (1, 1.0, 0, 10), (1, 1.0, 0, 10), (2, 1.5, 0, 150), (2, 1.5, 0, 150),
                (2, 1.5, 0, 150))
STOCK_ITEMS = ((20, 20, 20, 10, 20, 10), (20, 15, 20, 15, 15, 15), (20, 15, 15, 20, 15, 15))

F = tables.field
USAGE = F('star_usage', 'power', 'dire')
COST = F('star_cost', '4', 'points')
MOD = F('star_modifiers', 'level_4', 'special')
PITCH = F('preferred_pitch', 'technique', 'pitch_2')
STAMINA = F('stamina', 'star_pitch', 'cost')
AMOUNT = F('star_boost', 'curve', 'amount')
BOOST_MAX = F('star_boost', 'speed', 'max')
SHELL = F('item_odds', '2', 'shell')


def stock_dol() -> bytes:
    d4, d7 = bytearray(D4_SIZE), bytearray(D7_SIZE)

    def put(address, fmt, *values):
        blob, base = (d4, D4) if D4 <= address < D4 + D4_SIZE else (d7, D7)
        struct.pack_into(fmt, blob, address - base, *values)

    put(tables.STAR_USAGE, '>20B', *(v for row in STOCK_USAGE for v in row))
    put(tables.STAR_COST, '>6h', *STOCK_COSTS)
    put(tables.CHARGE_ODDS, '>8B', *(v for row in STOCK_CHARGE for v in row))
    put(tables.STAR_MODIFIERS, '>8b', *(v for row in STOCK_MODIFIERS for v in row))
    put(tables.PREFERRED_PITCH, '>16B', *(v for row in STOCK_PITCH for v in row),
        *(v for row in STOCK_CHANCE for v in row))
    put(tables.STAMINA - 6, '>8h', *STOCK_STAMINA_ROW)
    for i, row in enumerate(STOCK_BOOSTS):
        put(fields.STAR_BOOSTS + 12 * i, '>Ifhh', *row)
    put(tables.ITEM_ODDS, '>18B', *(v for row in STOCK_ITEMS for v in row))
    header = bytearray(dolfile.HEADER_SIZE)
    for slot, address, blob, offset in ((7 + 4, D4, d4, dolfile.HEADER_SIZE),
                                        (7 + 7, D7, d7, dolfile.HEADER_SIZE + D4_SIZE)):
        struct.pack_into('>I', header, slot * 4, offset)
        struct.pack_into('>I', header, 0x48 + slot * 4, address)
        struct.pack_into('>I', header, 0x90 + slot * 4, len(blob))
    struct.pack_into('>III', header, 0xD8, 0x80706D00, 0x9E180, 0x80004000)
    return bytes(header) + bytes(d4) + bytes(d7)


def image() -> dolfile.DolImage:
    return dolfile.DolImage(stock_dol())


def edits(values: dict) -> dict:
    return apply.edits_document(values)


def prepare(target: dolfile.DolImage, *docs, vanilla=True) -> apply.Prepared:
    return apply.prepare(target, list(docs), vanilla=image() if vanilla else None)


def written(target: dolfile.DolImage, *docs) -> dolfile.DolImage:
    apply.write(target, prepare(target, *docs))
    return target


def legacy_doc(**changes) -> dict:
    """An AI Gameplay Tool save with its default values (= stock), then ``changes`` per section."""
    classes = legacy.CLASSES
    doc = {'starConfig': {c: dict(zip(legacy.STAR_KEYS, STOCK_USAGE[i])) for i, c in enumerate(classes)},
           'pitchConfig': {c: {'pitch01': STOCK_PITCH[i][0], 'pitch2': STOCK_PITCH[i][1],
                               'prob01': STOCK_CHANCE[i][0], 'prob2': STOCK_CHANCE[i][1]}
                           for i, c in enumerate(classes)},
           'modifierConfig': {lvl: {'normal': STOCK_MODIFIERS[i][0], 'special': STOCK_MODIFIERS[i][1]}
                              for i, lvl in enumerate(legacy.LEVELS)},
           'staminaCost': -10,
           'chargeConfig': {c: {'charge01': STOCK_CHARGE[i][0], 'charge2': STOCK_CHARGE[i][1]}
                            for i, c in enumerate(classes)},
           'starBoostConfig': {s: {'addMult': b[0], 'amount': b[1], 'min': b[2], 'max': b[3], 'boost': '+1'}
                               for s, b in zip(legacy.BOOST_STATS, STOCK_BOOSTS)},
           'itemConfig': {k: list(STOCK_ITEMS[i]) for i, k in enumerate(legacy.ITEM_ROWS)},
           'starCostConfig': {'nonMain': 2, 'regular': 1, 'main': 1}}
    for path, value in changes.items():
        keys = path.split('__')
        node = doc
        for key in keys[:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
        last = keys[-1]
        if value is ...:
            del node[last]
        elif isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    return doc


class TableTests(unittest.TestCase):
    def test_every_field_is_unique_and_in_the_stock_sections(self):
        self.assertEqual(len(tables.FIELDS), 20 + 3 + 8 + 8 + 16 + 2 + 64 + 18)
        spans = set()
        for f in tables.FIELDS:
            for b in range(f.size):
                self.assertNotIn(f.address + b, spans, tables.label(f))
                spans.add(f.address + b)
            self.assertTrue(D4 <= f.address < D4 + D4_SIZE or D7 <= f.address < D7 + D7_SIZE)
        self.assertEqual({t.tab for t in tables.TABLES}, {k for k, _ in tables.TABS})
        self.assertEqual({t.section for t in tables.TABLES}, {k for k, _t, _l in tables.SECTIONS})

    def test_star_boosts_are_the_stat_editors_fields(self):
        self.assertIs(F('star_boost', 'curve', 'amount'), fields.global_field('star_boost', 'curve', 'amount'))

    def test_storage_limits(self):
        for f, bad in ((USAGE, 256), (USAGE, -1), (MOD, 128), (MOD, -129), (PITCH, 3), (COST, 0x8000),
                       (F('star_boost', 'curve', 'add/mult'), 3), (AMOUNT, float('inf')), (USAGE, 1.5)):
            with self.subTest(f=tables.label(f), value=bad):
                self.assertIsNotNone(f.problem(bad))
        self.assertEqual(MOD.unpack(MOD.pack(-128)), -128)

    def test_labels(self):
        self.assertEqual(tables.label(USAGE), 'Star usage odds: Power / Dire')
        self.assertEqual(tables.label(COST), 'Star costs: Non-captain')


class ReadTests(unittest.TestCase):
    def test_stock_values(self):
        values = read.values(image())
        self.assertEqual(len(values), len(tables.FIELDS))
        self.assertEqual((values[USAGE], values[COST], values[MOD], values[STAMINA]), (95, 50, 5, -10))
        self.assertAlmostEqual(values[F('star_boost', 'curveball speed', 'amount')], 1.1, places=6)
        self.assertEqual(read.missing_tables(image()), [])
        self.assertEqual(tables.problems(values), [])

    def test_dol_without_the_sections(self):
        from SluggiesTools.tests.test_roster_dev import synthetic_dol
        bare = dolfile.DolImage(synthetic_dol())
        self.assertEqual(read.values(bare), {})
        self.assertEqual(read.missing_tables(bare), [t.key for t in tables.TABLES])
        with self.assertRaisesRegex(apply.EditFileError, 'not hold these gameplay tables'):
            apply.settings_document(bare)
        with self.assertRaisesRegex(apply.EditFileError, 'not in main.dol'):
            apply.prepare(bare, [edits({USAGE: 50})])


class ApplyTests(unittest.TestCase):
    def test_edit_file_and_same_count(self):
        target = image()
        p = prepare(target, edits({USAGE: 50, COST: 75, AMOUNT: 2.25, MOD: 5}))
        self.assertEqual([c.field for c in p.changes], [USAGE, COST, AMOUNT])
        self.assertEqual(p.same, 1)                                            # MOD: stock 5 already
        apply.write(target, p)
        self.assertEqual(read.value(target, USAGE), 50)
        self.assertEqual(read.value(target, AMOUNT), 2.25)
        self.assertEqual(apply.describe(p), ['Star usage odds: Power / Dire 95 -> 50', 'Star costs: Non-captain 50 -> 75',
                                             'Star boosts: Curve / Amount 1.5 -> 2.25'])
        self.assertEqual(apply.summary(p), '3 values in 3 tables')

    def test_later_item_wins_and_reset_order(self):
        target = image()
        written(target, edits({USAGE: 50, SHELL: 30}))
        p = prepare(target, edits({USAGE: 60}), edits({USAGE: 70}))
        self.assertEqual([(c.field, c.after) for c in p.changes], [(USAGE, 70)])
        p = prepare(target, edits({USAGE: 60}), apply.Reset())
        self.assertEqual({(c.field, c.after, c.origin) for c in p.changes}, {(USAGE, 95, 'reset'), (SHELL, 20, 'reset')})
        self.assertEqual(p.same, 0)
        p = prepare(target, apply.Reset(), edits({SHELL: 25}))
        self.assertEqual({(c.field, c.after) for c in p.changes}, {(USAGE, 95), (SHELL, 25)})
        with self.assertRaisesRegex(apply.EditFileError, '1_Input'):
            prepare(target, apply.Reset(), vanilla=False)

    def test_refusals(self):
        target = image()
        bad = {
            'format': {'format': 'other', 'version': 1, 'values': {}},
            'version': {'format': apply.EDITS_FORMAT, 'version': 2, 'values': {}},
            'table': {'format': apply.EDITS_FORMAT, 'version': 1, 'values': {'nope': {}}},
            'row': {'format': apply.EDITS_FORMAT, 'version': 1, 'values': {'star_usage': {'nope': {'dire': 1}}}},
            'storage': {'format': apply.EDITS_FORMAT, 'version': 1, 'values': {'star_usage': {'power': {'dire': 300}}}},
            'whole': {'format': apply.EDITS_FORMAT, 'version': 1, 'values': {'star_usage': {'power': {'dire': 5.5}}}},
        }
        for name, doc in bad.items():
            with self.subTest(name), self.assertRaises(apply.EditFileError):
                prepare(target, doc)

    def test_cross_field_checks(self):
        target = image()
        for name, values in (('item row', {SHELL: 70}), ('usage + modifier', {USAGE: 123}),
                             ('modifier', {MOD: 33}), ('stamina', {STAMINA: 0}), ('star unit', {COST: 0})):
            with self.subTest(name), self.assertRaises(apply.EditFileError):
                prepare(target, edits(values))
        self.assertEqual(prepare(target, edits({USAGE: 122})).warnings, [])     # 122 + 5 = 127
        # a problem the game already holds is a warning for a file that does not change it
        bad = {**read.values(target), SHELL: 70}
        target.write(SHELL.address, bytes([70]))
        p = prepare(target, edits({USAGE: 50}))
        self.assertEqual(len(p.warnings), 1)
        self.assertIn('totals 150', p.warnings[0])
        self.assertTrue(tables.problems(bad))
        self.assertFalse(prepare(target, edits({SHELL: 20})).warnings)        # the edit fixes it


class SettingsTests(unittest.TestCase):
    def test_save_load_round_trip(self):
        edited = written(image(), edits({USAGE: 50, COST: 75, AMOUNT: 1.7, BOOST_MAX: -300, PITCH: 1, SHELL: 7}))
        doc = json.loads(json.dumps(apply.settings_document(edited, 'mine')))
        self.assertEqual((doc['format'], doc['version'], doc['name']), (apply.SETTINGS_FORMAT, 1, 'mine'))
        self.assertEqual(sum(len(cols) for rows in doc['values'].values() for cols in rows.values()), len(tables.FIELDS))
        self.assertEqual(doc['values']['star_boost']['curve']['amount'], 1.7)
        target = image()
        p = prepare(target, doc)
        self.assertEqual(len(p.changes), 6)
        self.assertEqual(p.same, len(tables.FIELDS) - 6)
        apply.write(target, p)
        self.assertEqual(target.to_bytes(), edited.to_bytes())
        self.assertFalse(prepare(target, doc).changes)

    def test_missing_keys_keep_the_game_value(self):
        doc = apply.settings_document(written(image(), edits({USAGE: 50})))
        del doc['values']['item_odds']
        target = written(image(), edits({SHELL: 7}))
        p = prepare(target, doc)
        self.assertEqual([(c.field, c.after) for c in p.changes], [(USAGE, 50)])
        self.assertEqual(len(p.notes), 1)
        self.assertIn("18 values not in the file keep the game's value (Item odds)", p.notes[0])
        doc['values']['item_odds'] = {'0': {'mushroom': 3}}
        with self.assertRaisesRegex(apply.EditFileError, 'unknown gameplay field'):
            prepare(target, doc)


class LegacyTests(unittest.TestCase):
    def test_detection(self):
        self.assertTrue(legacy.is_legacy(legacy_doc()))
        self.assertTrue(legacy.is_legacy({'starConfig': {}}))
        self.assertFalse(legacy.is_legacy({'format': 'x', 'starConfig': {}}))
        self.assertFalse(legacy.is_legacy(apply.settings_document(image())))

    def test_stock_file_stages_nothing(self):
        p = prepare(image(), legacy_doc())
        self.assertEqual((p.changes, p.notes, p.legacy), ([], [], True))

    def test_every_mapped_key(self):
        doc = legacy_doc(starConfig__Power__b5=50, starCostConfig__regular=1.5, chargeConfig__Speed__charge2=33,
                         modifierConfig__Level4__special=-7, pitchConfig__Technique__pitch2=1,
                         pitchConfig__Technique__prob2=44, staminaCost=-25, starBoostConfig__curve__amount=1.7,
                         starBoostConfig__speed__max=300, itemConfig__last__0=12)
        values = legacy.migrate(doc, read.values(image())).values
        expected = {USAGE: 50, COST: 75, F('charge_odds', 'speed', '2_strikes'): 33, MOD: -7, PITCH: 1,
                    F('preferred_pitch', 'technique', 'chance_2'): 44, STAMINA: -25, BOOST_MAX: 300, SHELL: 12}
        for f, v in expected.items():
            self.assertEqual(values[f], v, tables.label(f))
        self.assertEqual(values[AMOUNT], struct.unpack('>f', struct.pack('>f', 1.7))[0])
        target = written(image(), doc)
        self.assertEqual(read.value(target, COST), 75)
        self.assertEqual(read.value(target, BOOST_MAX), 300)

    def test_only_changed_blocks(self):
        migrated = legacy.migrate(legacy_doc(starConfig__Power__b5=50, starBoostConfig__curve__min=1),
                                  read.values(image()))
        self.assertEqual(migrated.blocks, ['Star usage odds', 'Star boosts: Curve'])
        self.assertEqual(len(migrated.values), 20 + 4)          # whole star usage table, one star boost row
        # the game's own edits stay where the file holds the stock values
        target = written(image(), edits({SHELL: 7, COST: 99}))
        written(target, legacy_doc(starConfig__Power__b5=50))
        self.assertEqual((read.value(target, SHELL), read.value(target, COST), read.value(target, USAGE)), (7, 99, 50))

    def test_clamps_and_wrap(self):
        doc = legacy_doc(starConfig__Power__b5=150.4, modifierConfig__Level4__special=-200,
                         starBoostConfig__speed__max=40000, starBoostConfig__speed__min=-5,
                         itemConfig__first__2=20.5, staminaCost=-40000)
        migrated = legacy.migrate(doc, read.values(image()))
        self.assertEqual(migrated.values[USAGE], 100)
        self.assertEqual(migrated.values[MOD], -128)
        self.assertEqual(migrated.values[BOOST_MAX], -25536)
        self.assertEqual(migrated.values[F('star_boost', 'speed', 'min')], 0)
        self.assertEqual(migrated.values[F('item_odds', '0', 'bob_omb')], 21)          # Math.round(20.5)
        self.assertEqual(migrated.values[STAMINA], -0x8000)
        self.assertEqual(len(migrated.notes), 5)            # 4 clamps, 1 wrap
        self.assertTrue(any('40000 is stored as -25536' in n for n in migrated.notes))

    def test_partial_file(self):
        doc = {'starConfig': legacy_doc(starConfig__Speed__b1=9)['starConfig']}
        target = written(image(), edits({SHELL: 7}))
        p = prepare(target, doc)
        self.assertEqual([(c.field, c.after) for c in p.changes], [(F('star_usage', 'speed', 'normal_0_1'), 9)])
        self.assertEqual(len(p.notes), 1)
        self.assertIn('starCostConfig, chargeConfig', p.notes[0])

    def test_cross_field_problems_are_warnings(self):
        p = prepare(image(), legacy_doc(modifierConfig__Level4__special=60, staminaCost=5))
        self.assertEqual(len(p.warnings), 6)            # five usage values over 67, the stamina cost
        self.assertTrue(any(w.startswith('Stamina costs: Star pitch 5') for w in p.warnings))
        with self.assertRaises(apply.EditFileError):    # an edit on top that touches a problem is refused
            prepare(image(), legacy_doc(modifierConfig__Level4__special=60), edits({USAGE: 90}))
        prepare(image(), legacy_doc(modifierConfig__Level4__special=60), edits({SHELL: 5}))

    def test_malformed(self):
        for name, doc in (('missing class', legacy_doc(starConfig__Speed=...)),
                          ('missing key', legacy_doc(chargeConfig__Power__charge2=...)),
                          ('text', legacy_doc(staminaCost='x')),
                          ('pitch type', legacy_doc(pitchConfig__Power__pitch01=3)),
                          ('boost op', legacy_doc(starBoostConfig__curve__addMult=0)),
                          ('items', legacy_doc(itemConfig__first=[1, 2])),
                          ('star cost', legacy_doc(starCostConfig__main=-1))):
            with self.subTest(name), self.assertRaises(apply.EditFileError):
                prepare(image(), doc)


class CarryTests(unittest.TestCase):
    def test_detect_apply(self):
        edited = written(image(), edits({USAGE: 50, AMOUNT: 2.0}))
        found = carry.detect(edited, image())
        self.assertEqual(set(found), {USAGE, AMOUNT})
        fresh = image()
        self.assertEqual(carry.apply(fresh, found), ['carried: 2 values (Star boosts, Star usage odds)'])
        self.assertEqual(fresh.to_bytes(), edited.to_bytes())
        self.assertEqual(carry.apply(fresh, {}), [])

    def test_stat_carry_skips_star_boosts(self):
        edited = written(image(), edits({AMOUNT: 2.0}))
        self.assertFalse(stat_carry.detect(edited, image()).globals)


class RunnerTests(unittest.TestCase):
    """Through ``runner.run`` on test_stat_editor_carry's roster DOL (its data section maps the D4 tables)."""

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(relocate, 'scan_refs', return_value=[]))
        self.enterContext(mock.patch.dict(os.environ, {gecko_compat.ACCEPT_ENV: '1'}))
        self.input_dir = os.path.join(self.tmp, '1_Input')
        os.makedirs(self.input_dir)
        self.dol_path = os.path.join(self.tmp, 'main.dol')
        for path in (self.dol_path, os.path.join(self.input_dir, 'main.dol')):
            with open(path, 'wb') as f:
                f.write(roster_vanilla().to_bytes())

    def current(self) -> dolfile.DolImage:
        with open(self.dol_path, 'rb') as f:
            return dolfile.DolImage(f.read())

    def test_settings_survive_roster_runs_and_remove(self):
        image_ = self.current()
        image_.write(AMOUNT.address, AMOUNT.pack(2.0))
        image_.write(SHELL.address, bytes([7]))
        with open(self.dol_path, 'wb') as f:
            f.write(image_.to_bytes())
        config = os.path.join(self.tmp, 'roster.json')
        with open(config, 'w') as f:
            json.dump(EXPANDED, f)
        report = runner.run(self.tmp, config_path=config, input_dir=self.input_dir)
        self.assertIn('[gameplay] carried: 2 values (Item odds, Star boosts)', report['log'])
        report = runner.run(self.tmp, remove_only=True, input_dir=self.input_dir)
        self.assertIn('[gameplay] carried: 2 values (Item odds, Star boosts)', report['log'])
        self.assertEqual(set(carry.detect(self.current(), roster_vanilla())), {AMOUNT, SHELL})
        self.assertEqual(read.value(self.current(), AMOUNT), 2.0)


class GameplayEnv(StatEnv):
    """``Env.gameplay`` on the synthetic stock DOL with ``docs`` ({item: parsed file}) as the files."""

    def __init__(self, docs: dict, stat_checks: dict | None = None, game: dolfile.DolImage | None = None):
        super().__init__(stat_checks or {})
        self.docs, self.game = docs, game or image()

    def gameplay(self, items):
        documents = [apply.Reset() if i == 'reset' else self.docs[i] for i in items]
        try:
            prepared = apply.prepare(self.game, documents, list(items), image())
        except apply.EditFileError as exc:
            raise slot_plan.PlanError(str(exc)) from exc
        return slot_plan.StatCheck(apply.summary(prepared) if prepared.changes else None,
                                   apply.describe(prepared) + prepared.notes, prepared.warnings)


class PlannerTests(unittest.TestCase):
    A = edits({USAGE: 50})
    B = edits({SHELL: 7})

    def plan(self, items, env):
        return slot_plan.plan_batch(make_state(), make_config(), slot_plan.parse_edits(items), env, STATE_FILE, NAMES)

    def test_one_step_before_the_re_read(self):
        b = self.plan([{'op': 'gameplay', 'file': 'a.json'}, {'op': 'gameplay', 'file': 'b.json'}],
                      GameplayEnv({'a.json': self.A, 'b.json': self.B}))
        self.assertTrue(b.ok, b.refused)
        self.assertEqual(b.commands, [('--apply-gameplay', 'a.json', 'b.json'), ('--roster-state',)])
        sections = b.to_json()['edits']
        self.assertEqual([(s['action'], s['target']) for s in sections], [('gameplay', '0xFF')] * 2)
        self.assertEqual(sections[0]['effects'], {'gameplay': '1 value in 1 table'})
        self.assertIn('Star usage odds: Power / Dire 95 -> 50', sections[0]['notes'])

    def test_after_stat_edits_and_the_rebuild(self):
        changed = slot_plan.StatCheck('1 value', ['x'])
        b = self.plan([{'op': 'gameplay', 'file': 'a.json'}, {'op': 'stat_edits', 'file': 's.json'},
                       {'op': 'stats', 'id': '0x0D', 'source': '0x09'}],
                      GameplayEnv({'a.json': self.A}, {'s.json': changed}))
        self.assertTrue(b.ok, b.refused)
        self.assertEqual([c[0] for c in b.commands],
                         ['--apply-stat-edits', '--roster', '--apply-gameplay', '--roster-state'])

    def test_reset_drops_earlier_edits(self):
        env = GameplayEnv({'a.json': self.A, 'b.json': self.B}, game=written(image(), edits({COST: 99})))
        b = self.plan([{'op': 'gameplay', 'file': 'a.json'}, {'op': 'gameplay', 'file': 'reset'},
                       {'op': 'gameplay', 'file': 'b.json'}], env)
        self.assertEqual(b.commands[0], ('--apply-gameplay', 'reset', 'b.json'))
        self.assertTrue(any('dropped: the later reset' in n for n in b.notes), b.notes)

    def test_nothing_unless_something_comes_first(self):
        same = edits({USAGE: 95})
        b = self.plan([{'op': 'gameplay', 'file': 'same.json'}], GameplayEnv({'same.json': same}))
        self.assertEqual((b.commands, [p.action for _e, p in b.skipped]), ([], ['gameplay']))
        b = self.plan([{'op': 'gameplay', 'file': 'a.json'}, {'op': 'gameplay', 'file': 'same.json'}],
                      GameplayEnv({'a.json': self.A, 'same.json': same}))
        self.assertEqual(b.commands[0], ('--apply-gameplay', 'a.json', 'same.json'))
        b = self.plan([{'op': 'stat_edits', 'file': 's.json'}, {'op': 'gameplay', 'file': 'same.json'}],
                      GameplayEnv({'same.json': same}, {'s.json': slot_plan.StatCheck('1 value', ['x'])}))
        self.assertEqual(b.commands[-2], ('--apply-gameplay', 'same.json'))     # its star boosts must win

    def test_checked_together(self):
        low = edits({F('star_modifiers', 'level_4', 'special'): -50})
        high = edits({USAGE: 125})
        b = self.plan([{'op': 'gameplay', 'file': 'high.json'}], GameplayEnv({'high.json': high}))
        self.assertTrue(b.refused)
        b = self.plan([{'op': 'gameplay', 'file': 'low.json'}, {'op': 'gameplay', 'file': 'high.json'}],
                      GameplayEnv({'low.json': low, 'high.json': high}))
        self.assertTrue(b.ok, b.refused)

    def test_parse_and_constants(self):
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.parse_edits([{'op': 'gameplay'}])
        self.assertEqual(slot_plan.GAMEPLAY_RESET, apply.RESET_ITEM)


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.out, self.inp = os.path.join(self.tmp, 'out.dol'), os.path.join(self.tmp, 'in.dol')
        with open(self.inp, 'wb') as f:
            f.write(stock_dol())
        with open(self.out, 'wb') as f:
            f.write(written(image(), edits({COST: 99})).to_bytes())

    def item(self, name, doc) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(doc, f)
        return path

    def test_layers_and_marks(self):
        snap = view.snapshot(self.out, self.inp, [self.item('a.json', edits({USAGE: 50}))])
        self.assertFalse(snap.readonly)
        self.assertEqual((snap.current[COST], snap.vanilla[COST], snap.pending[USAGE]), (99, 50, 50))
        drafts = {}
        view.set_draft(snap, drafts, SHELL, 70)
        view.set_draft(snap, drafts, MOD, 5)                    # the value it shows: no draft
        self.assertEqual(drafts, {SHELL: 70})
        marks = view.marks(snap, drafts)
        self.assertEqual((marks[COST], marks[USAGE], marks[SHELL]), (view.CHANGED, view.PENDING, view.PROBLEM))
        self.assertNotIn(MOD, marks)
        self.assertEqual(view.tooltip(snap, drafts, USAGE), '1_Input: 95  /  pending: 50')
        self.assertEqual(view.changed_count(snap), 2)
        self.assertEqual(view.section_values(snap, 'star_usage'), {USAGE: 95, COST: 50})

    def test_reset_item_and_errors(self):
        snap = view.snapshot(self.out, self.inp, ['reset'])
        self.assertEqual(snap.pending[COST], 50)
        snap = view.snapshot(self.out, self.inp, [os.path.join(self.tmp, 'gone.json')])
        self.assertIn('cannot be read', snap.error)
        self.assertEqual(snap.pending, snap.current)

    def test_read_only_without_an_output_game(self):
        snap = view.snapshot(os.path.join(self.tmp, 'missing.dol'), self.inp)
        self.assertTrue(snap.readonly)
        self.assertEqual(snap.current[COST], 50)
        self.assertIn('read-only', snap.source)

    def test_prune_drafts_and_staging_files(self):
        snap = view.snapshot(self.out, self.inp)
        drafts = {COST: 99, USAGE: 40}
        view.prune_drafts(snap, drafts)
        self.assertEqual(drafts, {USAGE: 40})
        staging = os.path.join(self.tmp, 'staging')
        kept = view.write_edits(staging, {USAGE: 40})
        gone = view.write_edits(staging, {USAGE: 41})
        copied = view.copy_for_staging(staging, self.item('mine.sluggiesgameplay', {}))
        self.assertTrue(os.path.basename(copied).startswith('load_1_mine'))
        view.prune_staging(staging, [kept])
        self.assertEqual(os.listdir(staging), [os.path.basename(kept)])
        self.assertFalse(os.path.exists(gone))

    def test_gui_texts(self):
        self.assertEqual(gui_grid.GAMEPLAY, slot_plan.GAMEPLAY)
        self.assertEqual(gui_grid.GAMEPLAY_RESET, apply.RESET_ITEM)
        self.assertEqual(gui_grid.GAMEPLAY_EXTENSION, apply.EXTENSION)
        self.assertEqual(gui_grid.gameplay_item_label('reset'), 'every value back to 1_Input')
        self.assertEqual(gui_grid.gameplay_item_label('/x/load_2_mine.json'), 'loaded from mine.json')
        self.assertEqual(gui_grid.gameplay_item_label('/x/edit_3.json'), 'changed values')
        state = {'gameplay': {'current': {'star_usage': {'power': {'dire': 50}}},
                              'vanilla': {'star_usage': {'power': {'dire': 95}}}}}
        self.assertIn('1 value differ', gui_grid.gameplay_risk(state))
        self.assertIsNone(gui_grid.gameplay_risk({'gameplay': {'current': {}, 'vanilla': {}}}))
        report = {'changes': [{'table': 'star_usage', 'row': 'power', 'column': 'dire', 'before': 95, 'after': 50}],
                  'values': [{'table': 'star_usage', 'row': 'power', 'column': 'dire', 'before': 95, 'after': 50},
                             {'table': 'item_odds', 'row': '0', 'column': 'boo', 'before': 10, 'after': 10}],
                  'same': 1, 'notes': ['n'], 'warnings': [], 'legacy': True}
        dialog = gui_grid.gameplay_load_dialog(report, 'cpu_behavior.json', view.cell_labels(), 2)
        texts = [t for t, _k in dialog.lines]
        self.assertTrue(dialog.can_apply)
        self.assertIn('AI Gameplay Tool file', dialog.title)
        self.assertIn('    Star usage odds: Power / Dire: 95 -> 50', texts)
        self.assertIn('Stage replaces the 2 pending gameplay edits.', texts)
        everything = gui_grid.gameplay_load_dialog(report, 'x', view.cell_labels(), 0, only_diff=False)
        self.assertIn('    Item odds: 6+ innings left / Boo: 10', [t for t, _k in everything.lines])
        refused = gui_grid.gameplay_load_dialog({'error': 'bad'}, 'x', {}, 0)
        self.assertFalse(refused.can_apply)

    def test_pending_titles_and_staging(self):
        pending = gui_grid.PendingEdits()
        pending.edits = [gui_grid.gameplay_edit('/x/edit_1.json'), {'op': 'rename', 'id': '0x00', 'text': 'A'}]
        self.assertEqual(pending.titles()[0], (None, 'gameplay settings: changed values'))
        staged = pending.staging(gui_grid.gameplay_edit('/x/load_1_a.json'), drop_op=gui_grid.GAMEPLAY)
        self.assertEqual([e['op'] for e in staged['edits']], ['rename', 'gameplay'])
        pending.discard_op(gui_grid.GAMEPLAY)
        self.assertEqual(gui_grid.staged_gameplay_items(pending), [])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.out, self.inp = os.path.join(self.tmp, '3_Output_Dat'), os.path.join(self.tmp, '1_Input')
        for d in (self.out, self.inp):
            os.makedirs(d)
            with open(os.path.join(d, 'main.dol'), 'wb') as f:
                f.write(stock_dol())
        self.enterContext(mock.patch.object(cli.slogger, 'configure'))

    def path(self, name: str, doc=None) -> str:
        path = os.path.join(self.tmp, name)
        if doc is not None:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(doc, f)
        return path

    def output(self) -> dolfile.DolImage:
        with open(os.path.join(self.out, 'main.dol'), 'rb') as f:
            return dolfile.DolImage(f.read())

    def main(self, *argv) -> int:
        return cli.main([*argv, '--output-dir', self.out, '--input-dir', self.inp])

    def test_apply_dry_run_report_save_reset(self):
        edit = self.path('edit.json', edits({USAGE: 50}))
        report = self.path('report.json')
        self.assertEqual(self.main('--apply', edit, '--dry-run', '--report', report), 0)
        self.assertEqual(self.output().to_bytes(), stock_dol())
        with open(report, encoding='utf-8') as f:
            got = json.load(f)
        self.assertEqual(got['changes'], [{'table': 'star_usage', 'row': 'power', 'column': 'dire', 'before': 95,
                                           'after': 50, 'origin': 'edit'}])
        self.assertEqual(self.main('--apply', edit), 0)
        self.assertEqual(read.value(self.output(), USAGE), 50)
        saved = self.path('mine' + apply.EXTENSION)
        self.assertEqual(self.main('--save', saved), 0)
        with open(saved, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['name'], 'mine')
        self.assertEqual(self.main('--apply', 'reset'), 0)
        self.assertEqual(self.output().to_bytes(), stock_dol())
        self.assertEqual(self.main('--apply', saved), 0)
        self.assertEqual(read.value(self.output(), USAGE), 50)

    def test_refusal_writes_nothing_and_reports(self):
        bad = self.path('bad.json', edits({USAGE: 50}) | {'version': 9})
        report = self.path('report.json')
        self.assertEqual(self.main('--apply', bad, '--report', report), 1)
        with open(report, encoding='utf-8') as f:
            self.assertIn('version 9', json.load(f)['error'])
        self.assertEqual(self.output().to_bytes(), stock_dol())


if __name__ == '__main__':
    unittest.main()
