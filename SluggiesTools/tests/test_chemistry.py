"""The Chemistry tab's model and its ``chemistry:<file>`` item (``Chemistry/``, ``StatEditor/apply.py``).

Synthetic DOLs as in ``test_stat_editor_carry`` (stock chemistry (a + b) % 4,
symmetric; ``EXPANDED`` adds the new IDs 0x66 and 0x67).
"""

import json
import os
import tempfile
import unittest

from SluggiesTools.Chemistry import layout, read, view
from SluggiesTools.Dol import dolfile
from SluggiesTools.Roster import ids
from SluggiesTools.StatEditor import apply, carry, cli, fields
from SluggiesTools.tests.test_stat_editor_apply import doc
from SluggiesTools.tests.test_stat_editor_carry import EXPANDED, Editor, copy, vanilla
from SluggiesTools.tests.test_roster_voice_stats_reassign import build as build_roster

BAD, NEUTRAL, GOOD = read.BAD, read.NEUTRAL, read.GOOD


def chem(*rows) -> apply.Chemistry:
    return apply.Chemistry('chem.json', apply.chemistry_document({(a, b): v for a, b, v in rows}))


def byte(image: dolfile.DolImage, a: int, b: int) -> int:
    place = carry.placement(image)
    return image.read(carry.chem_address(a, b, place.layouts, place.matrix), 1)[0]


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.image = build_roster(EXPANDED)[0]
        self.place = carry.placement(self.image)

    def test_three_regions(self):
        ed = Editor(self.image)
        ed.chem(0x01, 0x02, 0)            # stock x stock: one direction only
        ed.chem(0x66, 0x03, 2)            # stock x new: one byte
        ed.chem(0x66, 0x67, 0)            # new x new: one direction only
        ed.chem(0x67, 0x66, 2)
        values = read.directional(self.image, self.place)
        self.assertEqual((values[(0x01, 0x02)], values[(0x02, 0x01)]), (0, 3))
        self.assertEqual((values[(0x66, 0x03)], values[(0x03, 0x66)]), (2, 2))
        self.assertEqual((values[(0x66, 0x67)], values[(0x67, 0x66)]), (0, 2))
        self.assertEqual(len(values), 103 * 102)
        self.assertNotIn((0x05, 0x05), values)
        for (a, b), v in values.items():                 # every byte is the one chem_address names
            if a in (0x01, 0x40, 0x66, 0x67) and b in (0x02, 0x03, 0x64, 0x66, 0x67):
                self.assertEqual(v, byte(self.image, a, b), (a, b))

    def test_pairs_and_mismatches(self):
        ed = Editor(self.image)
        ed.chem(0x01, 0x02, 0)
        pairs = read.matrix(self.image)
        self.assertEqual(pairs[(0x01, 0x02)], (0, 3))
        self.assertEqual(len(pairs), 103 * 102 // 2)
        self.assertTrue(all(a < b for a, b in pairs))
        self.assertEqual(view.mismatches(pairs), [(0x01, 0x02, 0, 3)])
        self.assertEqual(view.mismatches(read.matrix(vanilla())), [])

    def test_baseline_is_the_unedited_roster(self):
        unedited = read.pairs(read.baseline(vanilla(), self.place))
        self.assertEqual(unedited, read.matrix(self.image))
        Editor(self.image).chem(0x66, 0x67, 0)
        self.assertNotEqual(unedited, read.matrix(self.image))
        stock = vanilla()
        self.assertEqual(read.pairs(read.baseline(stock, carry.placement(stock))), read.matrix(stock))

    def test_characters(self):
        people = read.characters(self.image, None, self.place)
        self.assertEqual([c.id for c in people], list(range(ids.STOCK_IDS)) + [0x66, 0x67])
        by_id = {c.id: c for c in people}
        self.assertEqual((by_id[0x66].kind, by_id[0x00].kind), ('new', 'stock'))
        self.assertTrue(by_id[0x4D].mii and by_id[0x64].mii)
        self.assertFalse(by_id[0x4C].mii or by_id[0x66].mii)
        self.assertEqual((by_id[0x4D].label, by_id[0x64].label, by_id[0x66].label), ('Mii 1', 'Mii 24', '0x66'))

    def test_a_dol_without_stat_tables(self):
        from SluggiesTools.tests.test_game_options import synthetic_dol
        self.assertEqual(read.matrix(dolfile.DolImage(synthetic_dol())), {})


class ItemTests(unittest.TestCase):
    def setUp(self):
        self.image = build_roster(EXPANDED)[0]

    def applied(self, *documents, vanilla_image=None):
        out = copy(self.image)
        prepared = apply.prepare(out, list(documents), vanilla=vanilla_image)
        apply.write(out, prepared)
        return out, prepared

    def test_both_directions_in_every_region(self):
        out, prepared = self.applied(chem((0x01, 0x04, GOOD), (0x03, 0x66, GOOD), (0x66, 0x67, BAD)))
        for a, b, v in ((0x01, 0x04, GOOD), (0x03, 0x66, GOOD), (0x66, 0x67, BAD)):
            self.assertEqual((byte(out, a, b), byte(out, b, a)), (v, v), (a, b))
        self.assertEqual(len(prepared.changes), 5)                    # stock x new is written once
        self.assertEqual(read.matrix(out)[(0x03, 0x66)], (GOOD, GOOD))

    def test_the_order_of_the_two_ids_does_not_matter(self):
        step = apply.Chemistry('x', {'format': apply.CHEMISTRY_FORMAT, 'version': 1, 'pairs': [['0x66', '0x03', 0]]})
        out, _prepared = self.applied(step)
        self.assertEqual(byte(out, 0x03, 0x66), BAD)

    def test_values_held_already_are_counted(self):
        now = byte(self.image, 0x00, 0x01)                            # (0 + 1) % 4 = 1
        _out, prepared = self.applied(chem((0x00, 0x01, now), (0x00, 0x02, BAD)))
        self.assertEqual((len(prepared.changes), prepared.same), (2, 2))

    def test_a_gone_id_is_skipped_with_a_warning(self):
        out, prepared = self.applied(chem((0x01, 0x70, GOOD), (0x01, 0x04, GOOD)))
        self.assertEqual(byte(out, 0x01, 0x04), GOOD)
        self.assertEqual(len(prepared.warnings), 1)
        self.assertIn('0x70 is not in this roster', prepared.warnings[0])

    def test_refusals(self):
        def raw(pairs, **header):
            return apply.Chemistry('x', dict({'format': apply.CHEMISTRY_FORMAT, 'version': 1, 'pairs': pairs},
                                             **header))
        cases = {
            'value 3': raw([['0x01', '0x02', 3]]),
            'value text': raw([['0x01', '0x02', 'good']]),
            'bool': raw([['0x01', '0x02', True]]),
            'twice': raw([['0x01', '0x02', 2], ['0x02', '0x01', 2]]),
            'self': raw([['0x01', '0x01', 2]]),
            'not an ID': raw([['Mario', '0x01', 2]]),
            'shape': raw([['0x01', '0x02']]),
            'pairs': raw({'0x01': 2}),
            'format': raw([], format=apply.FORMAT),
            'version': raw([], version=2),
        }
        for name, step in cases.items():
            with self.subTest(name), self.assertRaises(apply.EditFileError):
                apply.prepare(copy(self.image), [step])

    def test_no_hash_check(self):
        other = copy(self.image)
        Editor(other).set(0x05, fields.character_field('stats', 'batting arm'), 0x30)   # an unrelated DOL change
        prepared = apply.prepare(other, [chem((0x01, 0x04, GOOD))])
        self.assertEqual(len(prepared.changes), 2)

    def test_staging_order_decides_against_an_edit_file(self):
        names = {0x01: 'Luigi', 0x04: 'Peach'}
        stat = doc(self.image, {'0x01': {'chemistry': {'0x04': BAD}}})
        for order, expect, line in (
                ((stat, chem((0x01, 0x04, GOOD))), (GOOD, GOOD),
                 "Luigi (0x01) x Peach (0x04): the Stat Editor's value bad is replaced by the Chemistry tab's good"),
                ((chem((0x01, 0x04, GOOD)), stat), (BAD, GOOD),
                 "Luigi (0x01) x Peach (0x04): the Chemistry tab's value good is replaced by the Stat Editor's bad")):
            with self.subTest(expect=expect):
                out = copy(self.image)
                prepared = apply.prepare(out, list(order), names=names)
                apply.write(out, prepared)
                self.assertEqual((byte(out, 0x01, 0x04), byte(out, 0x04, 0x01)), expect)
                self.assertEqual(prepared.warnings, [line])

    def test_staging_order_decides_against_a_reset(self):
        unedited = byte(self.image, 0x01, 0x04)                        # (1 + 4) % 4 = 1
        out, prepared = self.applied(chem((0x01, 0x04, GOOD)), apply.Reset(0x01), vanilla_image=vanilla())
        self.assertEqual((byte(out, 0x01, 0x04), byte(out, 0x04, 0x01)), (unedited, unedited))
        self.assertEqual(len(prepared.warnings), 1)
        self.assertIn("the Chemistry tab's value good is replaced by the clear's neutral",
                      prepared.warnings[0])
        out, prepared = self.applied(apply.Reset(0x01), chem((0x01, 0x04, GOOD)), vanilla_image=vanilla())
        self.assertEqual((byte(out, 0x01, 0x04), byte(out, 0x04, 0x01)), (GOOD, GOOD))
        self.assertIn("the clear's value neutral is replaced by the Chemistry tab's good",
                      prepared.warnings[0])

    def test_staging_order_decides_against_a_copy(self):
        snapshot = {'format': apply.COPY_FORMAT, 'source': '0x05', 'target': '0x01', 'fields': {},
                    'chemistry': {'0x04': [BAD, BAD]}}
        paste = apply.Copy('snap', snapshot)
        out, prepared = self.applied(chem((0x01, 0x04, GOOD)), paste)
        self.assertEqual((byte(out, 0x01, 0x04), byte(out, 0x04, 0x01)), (BAD, BAD))
        self.assertIn("the Chemistry tab's value good is replaced by the paste's bad", prepared.warnings[0])
        out, prepared = self.applied(paste, chem((0x01, 0x04, GOOD)))
        self.assertEqual((byte(out, 0x01, 0x04), byte(out, 0x04, 0x01)), (GOOD, GOOD))
        self.assertIn("the paste's value bad is replaced by the Chemistry tab's good", prepared.warnings[0])

    def test_two_chemistry_items_replace_each_other_without_a_line(self):
        out, prepared = self.applied(chem((0x01, 0x04, GOOD)), chem((0x01, 0x04, BAD)))
        self.assertEqual(byte(out, 0x01, 0x04), BAD)
        self.assertEqual(prepared.warnings, [])

    def test_an_edit_makes_a_mismatch_bidirectional(self):
        Editor(self.image).chem(0x01, 0x04, BAD)                       # one direction only
        self.assertTrue(read.mismatch(read.matrix(self.image)[(0x01, 0x04)]))
        out, _prepared = self.applied(chem((0x01, 0x04, BAD)))
        self.assertEqual(read.matrix(out)[(0x01, 0x04)], (BAD, BAD))

    def test_carried_through_a_roster_run(self):
        out, _prepared = self.applied(chem((0x01, 0x04, GOOD), (0x03, 0x66, BAD), (0x66, 0x67, GOOD)))
        edits = carry.detect(out, vanilla())
        rebuilt = build_roster(EXPANDED)[0]
        carry.apply(rebuilt, edits)
        self.assertEqual(read.matrix(rebuilt), read.matrix(out))

    def test_parse_and_load_item(self):
        self.assertEqual(apply.parse_item('chemistry:C:/x/chemistry_1.json'), apply.Chemistry('C:/x/chemistry_1.json'))
        with tempfile.TemporaryDirectory() as tmp:
            path = view.write_item(tmp, {(0x04, 0x01): GOOD})
            step = apply.load_item(view.item_of(path))
            self.assertEqual(apply.chemistry_pairs(step.doc), {(0x01, 0x04): GOOD})


class CliTests(unittest.TestCase):
    def test_item_through_the_command_line(self):
        image = build_roster(EXPANDED)[0]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'main.dol'), 'wb') as f:
                f.write(image.to_bytes())
            path = view.write_item(tmp, {(0x01, 0x04): GOOD})
            self.assertEqual(cli.main(['--apply', view.item_of(path), '--dry-run', '--output-dir', tmp]), 0)
            with open(os.path.join(tmp, 'main.dol'), 'rb') as f:
                self.assertEqual(f.read(), image.to_bytes())
            self.assertEqual(cli.main(['--apply', view.item_of(path), '--output-dir', tmp]), 0)
            self.assertEqual(cli.main(['--apply', view.item_of(path), '--output-dir', tmp]), 0)    # fits again
            with open(os.path.join(tmp, 'main.dol'), 'rb') as f:
                out = dolfile.DolImage(f.read())
            self.assertEqual(read.matrix(out)[(0x01, 0x04)], (GOOD, GOOD))


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.out_dol = os.path.join(self.tmp, 'out', 'main.dol')
        self.in_dol = os.path.join(self.tmp, 'in', 'main.dol')
        self.staging = os.path.join(self.tmp, 'staging')
        os.makedirs(os.path.dirname(self.out_dol))
        os.makedirs(os.path.dirname(self.in_dol))
        self.image = build_roster(EXPANDED)[0]
        Editor(self.image).chem(0x02, 0x03, BAD)                       # an edit the game holds: both directions
        Editor(self.image).chem(0x03, 0x02, BAD)
        with open(self.out_dol, 'wb') as f:
            f.write(self.image.to_bytes())
        with open(self.in_dol, 'wb') as f:
            f.write(vanilla().to_bytes())

    def snap(self, items=()):
        return view.snapshot(self.out_dol, self.in_dol, list(items))

    def test_layers(self):
        path = view.write_item(self.staging, {(0x01, 0x04): GOOD})
        snap = self.snap([view.item_of(path)])
        self.assertFalse(snap.readonly)
        self.assertIsNone(snap.error)
        self.assertEqual(len(snap.characters), 103)
        self.assertEqual((snap.vanilla[(0x02, 0x03)], snap.current[(0x02, 0x03)]), ((1, 1), (BAD, BAD)))
        self.assertEqual((snap.current[(0x01, 0x04)], snap.pending[(0x01, 0x04)]), ((1, 1), (GOOD, GOOD)))
        marks = view.marks(snap, {(0x00, 0x04): BAD})
        self.assertEqual((marks[(0x02, 0x03)], marks[(0x01, 0x04)], marks[(0x00, 0x04)]),
                         (view.CHANGED, view.PENDING, view.DRAFT))
        self.assertNotIn((0x00, 0x08), marks)

    def test_pending_applies_the_staging_order(self):
        first = view.write_item(self.staging, {(0x01, 0x04): GOOD})
        second = view.write_item(self.staging, {(0x01, 0x04): BAD, (0x66, 0x67): GOOD})
        self.assertNotEqual(first, second)                             # two Stages stay two items
        snap = self.snap([view.item_of(first), view.item_of(second)])
        self.assertEqual((snap.pending[(0x01, 0x04)], snap.pending[(0x66, 0x67)]), ((BAD, BAD), (GOOD, GOOD)))
        snap = self.snap([view.item_of(second), view.item_of(first)])
        self.assertEqual(snap.pending[(0x01, 0x04)], (GOOD, GOOD))

    def test_pending_with_a_stat_editor_file_and_its_replaced_line(self):
        stat = os.path.join(self.tmp, 'stat_edits.json')
        with open(stat, 'w', encoding='utf-8') as f:
            json.dump(doc(self.image, {'0x01': {'chemistry': {'0x04': BAD}}}), f)
        path = view.write_item(self.staging, {(0x01, 0x04): GOOD})
        snap = self.snap([stat, view.item_of(path)])
        self.assertEqual(snap.pending[(0x01, 0x04)], (GOOD, GOOD))
        self.assertEqual(len(snap.warnings), 1)
        snap = self.snap([view.item_of(path), stat])
        self.assertEqual(snap.pending[(0x01, 0x04)], (BAD, GOOD))      # the editor's file names one direction
        self.assertEqual(view.marks(snap, {})[(0x01, 0x04)], view.MISMATCH)

    def test_an_unreadable_pending_item(self):
        snap = self.snap([view.item_of(os.path.join(self.tmp, 'gone.json'))])
        self.assertIn('cannot be read', snap.error)
        self.assertEqual(snap.pending, snap.current)

    def test_read_only_without_an_output_game(self):
        os.remove(self.out_dol)
        snap = self.snap()
        self.assertTrue(snap.readonly and snap.usable)
        self.assertEqual(len(snap.characters), 101)
        self.assertEqual(snap.current, read.matrix(vanilla()))
        os.remove(self.in_dol)
        self.assertFalse(self.snap().usable)

    def test_drafts(self):
        snap = self.snap()
        drafts = {}
        view.set_draft(snap, drafts, 0x04, 0x01, GOOD)
        self.assertEqual(drafts, {(0x01, 0x04): GOOD})
        self.assertEqual(view.value(snap, drafts, 0x04, 0x01), GOOD)
        view.set_draft(snap, drafts, 0x01, 0x04, NEUTRAL)              # back to what the game holds: no draft
        self.assertEqual(drafts, {})
        view.set_draft(snap, drafts, 0x01, 0x01, GOOD)                 # self chemistry is not edited
        view.set_draft(snap, drafts, 0x01, 0x70, GOOD)                 # not in the roster
        view.set_draft(snap, drafts, 0x01, 0x04, 3)
        self.assertEqual(drafts, {})
        drafts = {(0x01, 0x04): GOOD, (0x02, 0x03): BAD, (0x01, 0x70): GOOD}
        view.prune_drafts(snap, drafts)
        self.assertEqual(drafts, {(0x01, 0x04): GOOD})

    def test_a_mismatch_takes_any_draft(self):
        image = copy(self.image)
        Editor(image).chem(0x01, 0x04, BAD)
        with open(self.out_dol, 'wb') as f:
            f.write(image.to_bytes())
        snap = self.snap()
        self.assertEqual(view.mismatches(snap.current), [(0x01, 0x04, BAD, 1)])
        self.assertIsNone(view.value(snap, {}, 0x01, 0x04))
        self.assertIn('one-way', view.tooltip(snap, {}, 0x01, 0x04))
        drafts = {}
        view.set_draft(snap, drafts, 0x01, 0x04, BAD)                  # one direction holds it: still an edit
        self.assertEqual(drafts, {(0x01, 0x04): BAD})

    def test_connections_counts_and_partners(self):
        snap = self.snap()
        found = view.connections(snap, {})
        good, bad, odd = view.counts(found)
        self.assertEqual(odd, 0)
        self.assertEqual(good + bad + sum(1 for v in found.values() if v == 3), len(found))
        self.assertEqual(found[(0x02, 0x03)], BAD)
        self.assertNotIn((0x01, 0x04), found)                          # neutral
        drafted = view.connections(snap, {(0x01, 0x04): GOOD})
        self.assertEqual(view.counts(drafted)[0], good + 1)
        hidden = view.connections(snap, {}, hidden=[c.id for c in snap.characters if c.mii])
        self.assertTrue(all(a < ids.PLAYER_END or a >= ids.FIRST_NEW for a, _b in hidden))
        mine = view.partners(snap, {(0x01, 0x04): GOOD}, 0x04)
        self.assertEqual((mine[0x01], len(mine)), (GOOD, 102))
        self.assertNotIn(0x04, mine)

    def test_clear_all_and_copy_from(self):
        snap = self.snap()
        drafts = {}
        before = [o for o, v in view.partners(snap, drafts, 0x03).items() if v != NEUTRAL]
        self.assertEqual(view.clear_all(snap, drafts, 0x03), len(before))
        self.assertTrue(all(v == NEUTRAL for v in view.partners(snap, drafts, 0x03).values()))
        self.assertEqual(view.clear_all(snap, drafts, 0x03), 0)
        drafts = {}
        changed = view.copy_from(snap, drafts, 0x05, 0x02)
        mine, theirs = view.partners(snap, drafts, 0x05), view.partners(snap, drafts, 0x02)
        self.assertGreater(changed, 0)
        self.assertEqual(len(drafts), changed)
        for other, value in mine.items():
            if other != 0x02 and theirs[other] in read.VALUES:
                self.assertEqual(value, theirs[other], other)
        self.assertNotIn((0x02, 0x05), drafts)                         # the pair of the two stays
        self.assertEqual(view.copy_from(snap, drafts, 0x05, 0x05), 0)
        self.assertEqual(view.copy_from(snap, drafts, 0x05, 0x70), 0)

    def test_apply_to_variants(self):
        snap = self.snap()
        by_family = {}
        for c in snap.characters:
            by_family.setdefault(c.family, []).append(c.id)
        family = next(members for members in by_family.values() if len(members) > 1)
        head, others = family[0], family[1:]
        self.assertEqual(view.variants(snap, head), others)
        outside = next(c.id for c in snap.characters if c.id not in family)
        drafts = {}
        view.set_draft(snap, drafts, head, outside, GOOD if view.value(snap, {}, head, outside) != GOOD else BAD)
        target = view.value(snap, drafts, head, outside)
        self.assertGreater(view.apply_to_variants(snap, drafts, head), 0)
        for variant in others:
            self.assertEqual(view.value(snap, drafts, variant, outside), target)
            self.assertNotIn(view.key(head, variant), drafts)          # pairs inside the family stay
        self.assertEqual(view.apply_to_variants(snap, drafts, head), 0)
        self.assertEqual(view.variants(snap, 0x70), [])

    def test_cycle(self):
        snap = self.snap()
        drafts = {}
        seen = []
        for _click in range(3):
            view.cycle(snap, drafts, 0x01, 0x04)                       # neutral in the game
            seen.append(view.value(snap, drafts, 0x01, 0x04))
        self.assertEqual((seen, drafts), ([GOOD, BAD, NEUTRAL], {}))

    def test_toggle(self):
        snap = self.snap()
        drafts = {}
        seen = []
        for new in (GOOD, BAD, BAD, GOOD, GOOD):                       # add, replace, remove, add, remove
            self.assertEqual(view.toggle(snap, drafts, 0x04, 0x01, new), view.value(snap, drafts, 0x01, 0x04))
            seen.append(view.value(snap, drafts, 0x01, 0x04))
        self.assertEqual((seen, drafts), ([GOOD, BAD, NEUTRAL, GOOD, NEUTRAL], {}))

    def test_export_and_import_a_set(self):
        snap = self.snap()
        path = os.path.join(self.tmp, 'sets', 'mine')
        self.assertEqual(view.save_path(path), path + view.EXTENSION)
        self.assertEqual(view.save_path(path + '.json'), path + '.json')
        path = view.save_path(path)
        written, odd = view.export_set(path, snap, {(0x01, 0x04): GOOD})
        unknown = sum(1 for both in snap.current.values() if both[0] not in read.VALUES)     # the fixture's 3s
        self.assertEqual((written, odd), (len(snap.current) - unknown, unknown))
        drafts = {}
        self.assertEqual(view.import_set(path, snap, drafts), (1, 0))  # only the drafted pair differs from the game
        self.assertEqual(drafts, {(0x01, 0x04): GOOD})
        with open(self.out_dol, 'wb') as f:                            # a roster without the new IDs
            f.write(vanilla().to_bytes())
        stock = self.snap()
        drafts = {}
        changed, skipped = view.import_set(path, stock, drafts)
        self.assertGreater(skipped, 0)                                 # pairs of 0x66 / 0x67
        self.assertEqual(changed, len(drafts))
        self.assertEqual(view.value(stock, drafts, 0x02, 0x03), BAD)   # the edit the first game held
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'format': 'other'}, f)
        with self.assertRaises(apply.EditFileError):
            view.import_set(path, snap, {})

    def test_matrix_order(self):
        snap = self.snap()
        order = view.matrix_order(snap, {0x05: 0, 0x02: 0, 0x01: 1}, hidden=[0x03])
        self.assertEqual(order[:3], [0x02, 0x05, 0x01])
        self.assertNotIn(0x03, order)
        self.assertEqual(len(order), len(snap.characters) - 1)

    def test_tooltip_and_prune_staging(self):
        path = view.write_item(self.staging, {(0x02, 0x03): GOOD})
        old = view.write_item(self.staging, {(0x00, 0x04): GOOD})
        snap = self.snap([view.item_of(path)])
        self.assertEqual(view.tooltip(snap, {(0x02, 0x03): NEUTRAL}, 0x03, 0x02),
                         'unedited: neutral  /  game: bad  /  pending: good  /  edited, not staged: neutral')
        view.prune_staging(self.staging, [path])
        self.assertEqual((os.path.exists(path), os.path.exists(old)), (True, False))
        self.assertTrue(view.is_item(view.item_of(path)))
        self.assertEqual(view.item_path(view.item_of(path)), path)
        self.assertFalse(view.is_item('reset:0x01') or view.is_item(None))


def stat_steps(batch) -> list:
    return [c for c in batch.commands if c[0] == '--apply-stat-edits']


class PasteLayerTests(unittest.TestCase):
    def test_a_pending_paste_brings_the_sources_chemistry(self):
        current = {(1, 2): (GOOD, GOOD), (1, 3): (BAD, NEUTRAL), (2, 3): (NEUTRAL, NEUTRAL), (2, 4): (BAD, BAD),
                   (1, 4): (NEUTRAL, NEUTRAL), (3, 4): (GOOD, GOOD)}
        pending = dict(current)
        pending[(2, 3)] = (GOOD, GOOD)                                 # a staged chemistry edit of the target
        view.apply_pastes(pending, current, [(2, 1)])                  # paste 1 onto 2
        self.assertEqual(pending[(2, 3)], (BAD, NEUTRAL))              # 1 towards 3 / 3 towards 1: the edit is gone
        self.assertEqual(pending[(2, 4)], (NEUTRAL, NEUTRAL))
        self.assertEqual(pending[(1, 2)], (GOOD, GOOD))                # the pair of target and source stays
        self.assertEqual(pending[(3, 4)], (GOOD, GOOD))
        pending = dict(current)
        view.apply_pastes(pending, current, [(4, 1)])                  # the target has the higher ID: swapped sides
        self.assertEqual(pending[(3, 4)], (NEUTRAL, BAD))
        view.apply_pastes(pending, current, [(9, 1), (1, 1)])          # an unknown target, a paste onto itself
        self.assertEqual(len(pending), len(current))


class StagingTests(unittest.TestCase):
    """The chemistry item in the pending list and in the Patch Game plan."""

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = view.write_item(self.tmp, {(0x01, 0x04): GOOD, (0x04, 0x66): BAD})

    def test_prefix_is_the_same_everywhere(self):
        from SluggiesTools import gui_grid
        from SluggiesTools.Roster import slot_plan
        self.assertEqual({apply.CHEMISTRY_PREFIX, slot_plan.CHEMISTRY_PREFIX, gui_grid.CHEMISTRY_PREFIX},
                         {'chemistry:'})
        self.assertEqual(gui_grid.chemistry_edit(self.path),
                         {'op': gui_grid.STAT_EDITS, 'id': '0xFF', 'file': view.item_of(self.path)})

    def test_pending_helpers(self):
        from SluggiesTools import gui_grid
        pending = gui_grid.PendingEdits()
        stat = {'op': gui_grid.STAT_EDITS, 'id': '0xFF', 'file': os.path.join(self.tmp, 'stat_edits.json')}
        chemistry = gui_grid.chemistry_edit(self.path)
        pending.edits = [stat, gui_grid.stat_reset_edit(0x05), chemistry, gui_grid.copy_edit(0x09, 0x02)]
        pending.sections = [{'action': gui_grid.STAT_EDITS, 'target': '0xFF', 'edit': dict(e)}
                            for e in (stat, chemistry)]
        self.assertEqual(gui_grid.staged_stat_files(pending), [stat['file']])
        self.assertEqual(gui_grid.staged_chemistry_files(pending), [self.path])
        self.assertEqual(gui_grid.staged_stat_items(pending), [stat['file'], 'reset:0x05', chemistry['file']])
        self.assertEqual(gui_grid.pending_pastes(pending), [(0x09, 0x02)])
        self.assertEqual(gui_grid.stat_file_ids(chemistry['file']), frozenset({0x01, 0x04, 0x66}))
        self.assertTrue(pending.has_stats(0x66) and not pending.has_stats(0x02))
        self.assertEqual(gui_grid._edit_title(chemistry), 'Pending: chemistry changes')
        pending.discard_stat_items(chemistry=True)
        self.assertEqual([e['op'] for e in pending.edits], [gui_grid.STAT_EDITS, gui_grid.STAT_RESET, gui_grid.COPY])
        self.assertEqual((gui_grid.staged_chemistry_files(pending), len(pending.sections)), ([], 1))
        pending.edits.append(chemistry)
        pending.discard_stat_items(chemistry=False)
        self.assertEqual((gui_grid.staged_stat_files(pending), gui_grid.staged_chemistry_files(pending)),
                         ([], [self.path]))

    def plan(self, items, checks, replaced=(), pairs=()):
        from SluggiesTools.Roster import slot_plan
        from SluggiesTools.tests.test_roster_voice_stats_reassign import STATE_FILE, NAMES, make_config, make_state

        class Env(slot_plan.Env):
            def stat_edits(self, path):
                return checks[path]

            def stat_replaced(self, found):
                self.asked = list(found)
                return list(replaced)

            def chemistry_pairs(self, item):
                return list(pairs)
        env = Env()
        batch = slot_plan.plan_batch(make_state(), make_config(), slot_plan.parse_edits(items), env, STATE_FILE,
                                     NAMES)
        return batch, env

    def test_plan_one_step_in_staging_order_with_the_replaced_lines(self):
        from SluggiesTools.Roster import slot_plan
        item = view.item_of(self.path)
        changed = slot_plan.StatCheck('2 values on 2 characters', ['a line'])
        batch, env = self.plan([{'op': 'stat_edits', 'file': 'a.json'}, {'op': 'stat_edits', 'file': item}],
                               {'a.json': changed, item: changed}, replaced=['x is replaced by y'])
        self.assertTrue(batch.ok, batch.refused)
        self.assertEqual(stat_steps(batch), [('--apply-stat-edits', 'a.json', item)])
        self.assertEqual((env.asked, batch.warnings), (['a.json', item], ['x is replaced by y']))
        self.assertEqual(batch.to_json()['merged'][-1], {'op': 'stat_edits', 'id': '0xFF', 'file': item})

    def test_plan_keeps_an_item_the_game_holds_when_an_earlier_item_may_change_it(self):
        from SluggiesTools.Roster import slot_plan
        item = view.item_of(self.path)
        nothing, changed = slot_plan.StatCheck(None), slot_plan.StatCheck('1 value on 1 character')
        batch, _env = self.plan([{'op': 'stat_edits', 'file': item}], {item: nothing})
        self.assertEqual((stat_steps(batch), len(batch.skipped)), ([], 1))
        batch, _env = self.plan([{'op': 'stat_edits', 'file': 'a.json'}, {'op': 'stat_edits', 'file': item}],
                                {'a.json': changed, item: nothing})
        self.assertEqual(stat_steps(batch), [('--apply-stat-edits', 'a.json', item)])
        batch, _env = self.plan([{'op': 'stat_edits', 'file': item}, {'op': 'stat_edits', 'file': 'a.json'}],
                                {'a.json': changed, item: nothing})
        self.assertEqual(stat_steps(batch), [('--apply-stat-edits', 'a.json')])

    def test_a_pending_paste_of_the_same_character_warns_in_both_orders(self):
        from types import SimpleNamespace as Row
        from SluggiesTools.Roster import slot_plan
        from SluggiesTools.tests.test_roster_voice_stats_reassign import make_state
        item = view.item_of(self.path)

        class Env(slot_plan.Env):
            def chemistry_pairs(self, found):
                return [(0x01, 0x0D), (0x0D, 0x05), (0x0D, 0x09), (0x05, 0x06)]
        paste = (Row(op=slot_plan.COPY, cid=0x0D, source=0x09),
                 Row(commands=[('--roster', '--state', 'x'), ('--apply-stat-edits', slot_plan.COPY_PREFIX + 's.json')]))
        chemistry = (Row(op=slot_plan.STAT_EDITS, cid=slot_plan.GAME_WIDE, source=None),
                     Row(commands=[('--apply-stat-edits', item)]))
        other = (Row(op=slot_plan.COPY, cid=0x09, source=0x0D),
                 Row(commands=[('--apply-stat-edits', slot_plan.COPY_PREFIX + 't.json')]))
        for order in ((paste, chemistry, other), (other, chemistry, paste)):
            lines = slot_plan._chemistry_warnings(make_state(), Row(plans=list(order)), Env())
            self.assertEqual(len(lines), 1, lines)                     # 0x09: only its pair with its source
            self.assertIn('replaces its chemistry: 2 staged chemistry values of', lines[0])   # not the pair with 0x09
            self.assertIn('Patch the paste first, then edit its chemistry.', lines[0])
        self.assertEqual(slot_plan._chemistry_warnings(make_state(), Row(plans=[paste, other]), Env()), [])

    def test_real_env_reads_the_replaced_lines_and_the_pairs(self):
        from SluggiesTools.Roster import slot_cli
        image = build_roster(EXPANDED)[0]
        stat = os.path.join(self.tmp, 'stat_edits.json')
        with open(stat, 'w', encoding='utf-8') as f:
            json.dump(doc(image, {'0x01': {'chemistry': {'0x04': BAD}}}), f)
        env = slot_cli.FileEnv.__new__(slot_cli.FileEnv)
        env.image, env.dat, env.input_dir = image, None, self.tmp
        item = view.item_of(self.path)
        self.assertEqual(sorted(env.chemistry_pairs(item)), [(0x01, 0x04), (0x04, 0x66)])
        self.assertEqual(env.chemistry_pairs(view.item_of(os.path.join(self.tmp, 'gone.json'))), [])
        lines = env.stat_replaced([stat, item])
        self.assertEqual(lines, ["0x01 x 0x04: the Stat Editor's value bad is replaced by the Chemistry tab's good"])
        self.assertEqual(env.stat_edits(item).summary, '3 values on 2 characters')    # stock x new: one byte


def clusters(*sizes, first=0):
    """IDs and good pairs of complete clusters of ``sizes`` members, with one pair between neighbours."""
    members, good, at = [], [], first
    for size in sizes:
        own = list(range(at, at + size))
        good += [(a, b) for a in own for b in own if a < b]
        if members:
            good.append((members[-1][0], own[0]))
        members.append(own)
        at += size
    return [c for own in members for c in own], good, members


class LayoutTests(unittest.TestCase):
    def test_two_clusters_are_two_groups(self):
        all_ids, good, (left, right) = clusters(6, 5)
        found = layout.groups(all_ids + [40], good)
        self.assertEqual({found[c] for c in left}, {0})                # the larger group first
        self.assertEqual({found[c] for c in right}, {1})
        self.assertEqual(found[40], 2)                                 # no good pair: a group of its own
        self.assertEqual(layout.groups([], []), {})

    def test_deterministic_and_no_overlap(self):
        all_ids, good, _members = clusters(9, 7, 4, 1, 1)
        first = layout.arrange(all_ids, good)
        again = layout.arrange(list(reversed(all_ids)), list(reversed(good)))
        self.assertEqual(first.positions, again.positions)
        self.assertEqual(first.groups, again.groups)
        self.assertEqual(sorted(first.positions), all_ids)
        self.assertEqual(layout.overlapping(first.positions), [])
        left, top, right, bottom = first.bounds
        self.assertTrue(right - left < 40 and bottom - top < 40, first.bounds)

    def test_members_of_a_group_are_nearer_than_the_other_group(self):
        all_ids, good, (left, right) = clusters(8, 8)
        p = layout.arrange(all_ids, good).positions

        def centre(own):
            return (sum(p[c][0] for c in own) / len(own), sum(p[c][1] for c in own) / len(own))
        (lx, ly), (rx, ry) = centre(left), centre(right)
        for own, (mx, my), (ox, oy) in ((left, (lx, ly), (rx, ry)), (right, (rx, ry), (lx, ly))):
            for c in own:
                self.assertLess((p[c][0] - mx) ** 2 + (p[c][1] - my) ** 2, (p[c][0] - ox) ** 2 + (p[c][1] - oy) ** 2)

    def test_miis_leave_the_others_in_place(self):
        all_ids, good, _members = clusters(7, 6, 3)
        miis = [0x4D, 0x4E, 0x4F, 0x50]
        good_with = good + [(0, 0x4D), (1, 0x4D), (8, 0x4E), (0x4E, 0x4F)]
        without = layout.arrange(all_ids, good)
        with_miis = layout.arrange(all_ids + miis, good_with, miis)
        self.assertEqual({c: with_miis.positions[c] for c in all_ids}, without.positions)
        self.assertEqual(layout.overlapping(with_miis.positions), [])
        self.assertEqual({with_miis.groups[c] for c in miis}, {-1})
        only = layout.arrange(miis, [], miis)                          # nothing but Miis
        self.assertEqual((sorted(only.positions), layout.overlapping(only.positions)), (miis, []))

    def test_kept_positions_and_a_new_id(self):
        all_ids, good, (left, _right) = clusters(6, 6)
        first = layout.arrange(all_ids, good)
        moved = dict(first.positions)
        moved[0] = (50.0, 50.0)                                        # as if the user's file said so
        new = 0x66
        later = layout.arrange(all_ids + [new], good + [(new, c) for c in left[1:]], kept=moved)
        self.assertEqual({c: later.positions[c] for c in all_ids}, moved)
        self.assertEqual(layout.overlapping(later.positions), [])
        nx, ny = later.positions[new]
        self.assertTrue(all(abs(nx - moved[c][0]) < 6 and abs(ny - moved[c][1]) < 6 for c in left[1:]))
        gone = layout.arrange(all_ids[1:], good, kept=moved)           # an ID the roster no longer has
        self.assertNotIn(0, gone.positions)

    def test_255_nodes(self):
        sizes = [25, 20, 20, 15, 15, 12, 10, 10, 8, 8, 6, 6, 5, 5, 4, 3, 2, 1, 1]
        all_ids, good, _members = clusters(*sizes)
        miis = list(range(len(all_ids), 255))
        good += [(m, (m * 7) % len(all_ids)) for m in miis]
        found = layout.arrange(all_ids + miis, good, miis)
        self.assertEqual(len(found.positions), 255)
        self.assertEqual(layout.overlapping(found.positions), [])

    def test_save_and_load(self):
        all_ids, good, _members = clusters(5, 4)
        found = layout.arrange(all_ids, good)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'sub', 'layout.json')
            self.assertEqual(layout.load(path), {})
            layout.save(path, found.positions)
            kept = layout.load(path)
            self.assertEqual(sorted(kept), all_ids)
            self.assertEqual(layout.overlapping(kept), [])
            self.assertEqual(layout.arrange(all_ids, good, kept=kept).positions, kept)
            with open(path, 'w', encoding='utf-8') as f:
                f.write('{"format": "other"}')
            self.assertEqual(layout.load(path), {})

    def test_focus_ring(self):
        ring = layout.focus_ring([1, 2, 3], [4])
        self.assertTrue(all(ring[c][0] < 0 for c in (1, 2, 3)) and ring[4][0] > 0)
        many = layout.focus_ring(list(range(1, 60)), list(range(60, 100)))
        self.assertEqual(len(many), 99)
        many[0] = (0.0, 0.0)
        self.assertEqual(layout.overlapping(many), [])
        self.assertEqual(layout.focus_ring([], []), {})


if __name__ == '__main__':
    unittest.main()
