"""Animation banks of a slot (files 6-14): ``Roster/anim.py``, the planner's ``anim`` edits, the patcher's
``WriteSlotAnimation`` and the GUI texts.

Banks and skeletons are synthetic: a bank of the right sequence count whose tracks drive the given track ids, a
model block whose ACT section carries a kind-3 bone-to-track table.
"""

import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.tests import test_equipment as teq
from SluggiesTools.tests import test_slot_target as tst
from SluggiesTools.tests.test_slot_target import OTHER, SOURCE, TARGET, main

import SlotTarget  # noqa: E402 (path set up by test_slot_target)

from SluggiesTools import gui_grid
from SluggiesTools.Roster import anim, slot_plan
from SluggiesTools.tests import test_slot_plan as tsp

QUAT = 1 << 3                                   # a track with a quaternion channel
OPAQUE = 0x20                                   # a channel-less track (opaque words)


def bank(file, ids, magic=anim.MAGICS[0], count=None, opaque=(), pad=0):
    """An ANM bank for ``file``: ``count`` sequences (default: the file's stock count), each animating ``ids``."""
    count = anim.SEQUENCES[file][0] if count is None else count
    tracks = [(i, QUAT) for i in sorted(ids)] + [(i, OPAQUE) for i in opaque]
    seq_at = anim.HEADER_SIZE
    track_at = seq_at + anim.SEQUENCE_SIZE * count
    out = bytearray(struct.pack('>IIHHHHII', magic, seq_at, 0, count, count * len(tracks), 0, 0, 0))
    for s in range(count):
        out += struct.pack('>IIHH', 0, track_at + anim.TRACK_SIZE * len(tracks) * s, len(tracks), 0)
    for _s in range(count):
        for track_id, track_type in tracks:
            out += struct.pack('>fIHHBBBB', 1.0, 0, 0, track_id, 0x3E, track_type, 0, 1)
    return bytes(out) + bytes(pad)


def model(track_ids, parents=tst.TOAD, geo='kinopio_r.gpl'):
    """A model block whose bones read ``track_ids`` (bone order; 0xFFFF: none)."""
    block = bytearray(tst.model_block(geo, parents))
    act = 0x20
    ids = list(track_ids) + [anim.NO_TRACK] * (len(parents) - len(track_ids))
    ptr = len(block) - act
    payload = struct.pack(f'>{len(ids)}H', *ids)
    block += struct.pack('>IHHI', anim.DESCRIPTOR_SIZE + len(payload), anim.TRACK_KIND, 0, anim.DESCRIPTOR_SIZE)
    block += payload
    struct.pack_into('>II', block, act + 0x18, anim.DESCRIPTOR_SIZE + len(payload), ptr)
    return bytes(block)


class BankTests(unittest.TestCase):
    def test_read_bank(self):
        b = anim.read_bank(bank(7, {1, 2}, opaque=(0x4000,)))
        self.assertEqual(b.count, 28)
        self.assertEqual(b.tracks, {1, 2})                       # channel-less tracks drive nothing
        self.assertEqual(anim.bank_file(b), 7)

    def test_every_file_has_its_count(self):
        for file in anim.FILES:
            self.assertEqual(anim.bank_file(anim.read_bank(bank(file, {1}))), file)
        self.assertEqual(anim.bank_file(anim.read_bank(bank(12, {1}, count=22))), 12)     # the Miis' bank 12

    def test_refusals(self):
        for data, message in ((b'\x00' * 8, 'too short'), (bank(7, {1}, magic=0x12345678), 'not an animation bank'),
                              (bank(7, {1})[:0x40], 'runs past')):
            with self.assertRaisesRegex(anim.AnimError, message):
                anim.read_bank(data)
        with self.assertRaisesRegex(anim.AnimError, '3 animations, which matches none'):
            anim.bank_file(anim.read_bank(bank(7, {1}, count=3)))

    def test_skeleton_tracks(self):
        self.assertEqual(anim.skeleton_tracks(model([0, 2, 5])), {0, 2, 5})
        self.assertEqual(anim.skeleton_tracks(model([0, anim.NO_TRACK, 5])), {0, 5})
        self.assertIsNone(anim.skeleton_tracks(tst.model_block('x.gpl', [None])))         # no user data
        self.assertIsNone(anim.skeleton_tracks(None))
        self.assertIsNone(anim.skeleton_tracks(b'\x00' * 4))

    def test_problems(self):
        stock = anim.read_bank(bank(7, {1, 2, 0x8208}))          # 0x8208: no bone has it, the game ignores it
        skeleton = frozenset({1, 2, 3})
        self.assertEqual(anim.problems(stock, 7, stock, skeleton), ([], []))
        self.assertEqual(anim.problems(anim.read_bank(bank(7, {1, 2, 3})), 7, stock, skeleton), ([], []))
        errors, _w = anim.problems(anim.read_bank(bank(7, {1, 0x50, 0x51})), 7, stock, skeleton)
        self.assertRegex(errors[0], '2 track ids .* no bone for \\(0x50, 0x51\\): it was made for another skeleton')
        errors, _w = anim.problems(anim.read_bank(bank(12, {1}, count=22)), 12,
                                   anim.read_bank(bank(12, {1})), skeleton)
        self.assertEqual(errors, ["it holds 22 animations, the slot's animation bank 12 24"])
        errors, warnings = anim.problems(anim.read_bank(bank(7, {1})), 7, stock, skeleton)
        self.assertEqual(errors, [])
        self.assertRegex(warnings[0], '1 bone the stock bank animates has no track in it \\(0x2\\)')


# --------------------------------------------------------------------------
# Patcher
# --------------------------------------------------------------------------

ANIM_VANILLA = {(chunk, f): bank(f, {1, 2}, pad=chunk) for chunk in (SOURCE, TARGET, OTHER) for f in anim.FILES}
ALL = {**teq.ALL, **ANIM_VANILLA}


class AnimHarness(tst.SlotHarness):
    FILES = tuple(range(15))
    VANILLA = ALL

    def resolve(self, cid=0x13, chunk=TARGET):
        return mock.patch.object(SlotTarget, 'resolve_dir', return_value=(cid, chunk))


class WriteSlotAnimationTests(AnimHarness):
    def test_a_bank_reaches_the_target_route_only(self):
        new = bank(7, {1, 2}, magic=anim.MAGICS[2])
        with self.resolve():
            self.assertTrue(main.WriteSlotAnimation('0x13', 7, new))
        offset, length = self.route((TARGET, 7))
        self.assertGreaterEqual(offset, tst.BASE_SIZE)
        self.assertEqual(self.live((TARGET, 7)), new)
        for route in ((SOURCE, 7), (OTHER, 7), (TARGET, 6), (TARGET, 8), (TARGET, 0)):
            self.assert_vanilla_route(route)

    def test_a_bank_that_does_not_fit_writes_nothing(self):
        before = (self.output_dol.read_bytes(), self.output_dat.read_bytes())
        with self.resolve():
            self.assertFalse(main.WriteSlotAnimation('0x13', 7, bank(7, {1, 0x50})))       # another skeleton
            self.assertFalse(main.WriteSlotAnimation('0x13', 7, bank(8, {1, 2})))          # another bank's count
            self.assertFalse(main.WriteSlotAnimation('0x13', 7, b'not an anm file at all, no'))
            self.assertFalse(main.WriteSlotAnimation('0x13', 2, bank(7, {1, 2})))          # not an animation file
        self.assertEqual((self.output_dol.read_bytes(), self.output_dat.read_bytes()), before)

    def test_the_vanilla_bank_and_a_reset_return_to_the_baseline(self):
        with self.resolve():
            self.assertTrue(main.WriteSlotAnimation('0x13', 14, bank(14, {1, 2}, magic=anim.MAGICS[1])))
            self.assertTrue(main.WriteSlotAnimation('0x13', 14, ANIM_VANILLA[(TARGET, 14)]))
            self.assert_vanilla_route((TARGET, 14))
            self.assertTrue(main.WriteSlotAnimation('0x13', 9, bank(9, {1})))
            self.assertTrue(main.ClearEquipment('0x13', 9))                               # --unpatch --target-file 9
            self.assert_vanilla_route((TARGET, 9))
            self.assertFalse(main.ClearEquipment('0x13', 15))


# --------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------

PICK = '/m/19 Luigi/anm/7.anm'
SKELETON = model([1, 2, 3])
STOCK = {f: bank(f, {1, 2}) for f in anim.FILES}


def anim_state(custom=None, shared=0):
    """``tsp.make_state()`` with animation banks; ``custom``: ``{cid: [files that are not vanilla]}``."""
    st = tsp.make_state()
    for c in st['characters']:
        c['animations'] = [{'file': f, 'offset': 0x1000 * f, 'length': 64, 'sha1': f'{c["id"]:02x}{f}',
                            'shared_with': shared, 'vanilla': f not in (custom or {}).get(c['id'], ())}
                           for f in anim.FILES]
    return st


class AnimEnv(tsp.FakeEnv):
    def __init__(self, current=None, limit=None, **kw):
        super().__init__(**kw)
        self.current, self.limit = current or {}, limit
        self.vanilla_calls = []

    def vanilla_block(self, directory, file):
        self.vanilla_calls.append((directory, file))
        return SKELETON if file == slot_plan.HIGH_FILE else STOCK.get(file)

    def current_block(self, char, role):
        return SKELETON if role == 'high' else None

    def current_anim(self, char, file):
        return self.current.get((char['id'], file), STOCK[file])

    def anim_size_limit(self, file):
        return self.limit


def pick(data, path=PICK):
    b = anim.read_bank(data)
    return slot_plan.AnimPick(path, data, b, anim.bank_file(b))


class PlanAnimTests(unittest.TestCase):
    def plan(self, cid, data=None, env=None, st=None, config=None):
        return slot_plan.plan_anim(st or anim_state(), config or tsp.make_config(), cid,
                                   pick(data or bank(7, {1, 2, 3})), env or AnimEnv(), tsp.STATE_FILE)

    def test_a_stock_slot_gets_one_targeted_write(self):
        env = AnimEnv()
        plan = self.plan(0x0D, env=env)
        self.assertEqual(plan.commands, [('--write-slot-animation', '0x0D', '7', PICK), ('--roster-state',)])
        self.assertIsNone(plan.config)
        self.assertEqual(plan.effects['animations'], {7: 'animation bank 7 from 7.anm'})
        self.assertIn('28 animations, all replaced', plan.notes[0])
        self.assertIn((31, 7), env.vanilla_calls)                      # Red Toad's own directory
        self.assertEqual(plan.to_json()['anim']['file'], 7)

    def test_a_bank_for_another_skeleton_is_refused(self):
        with self.assertRaisesRegex(slot_plan.PlanError, '7.anm does not fit Red Toad.*made for another skeleton'):
            self.plan(0x0D, bank(7, {1, 0x60}))

    def test_bones_left_out_are_a_warning(self):
        plan = self.plan(0x0D, bank(7, {1}))
        self.assertTrue(any('no track in it' in w for w in plan.warnings))

    def test_a_new_id_without_a_directory_gets_one_from_its_template(self):
        env = AnimEnv()
        plan = self.plan(0x66, env=env)
        self.assertEqual(plan.commands, [('--roster', '--state', tsp.STATE_FILE),
                                         ('--write-slot-animation', '0x66', '7', PICK), ('--roster-state',)])
        self.assertEqual(tsp.ids_entry(plan.config, 0x66)['model'], {'from': '0x04'})
        self.assertIn((22, 7), env.vanilla_calls)                      # the template's (Peach's) directory

    def test_a_new_id_with_its_own_directory_checks_against_its_source(self):
        env = AnimEnv()
        plan = self.plan(0x67, env=env)
        self.assertIsNone(plan.config)
        self.assertIn((27, 7), env.vanilla_calls)                      # Bowser's files

    def test_the_stock_bank_resets_a_custom_one(self):
        st = anim_state({0x0D: [7]})
        plan = self.plan(0x0D, STOCK[7], env=AnimEnv(current={(0x0D, 7): bank(7, {1, 2, 3})}), st=st)
        self.assertEqual(plan.commands, [('--unpatch', '--target-id', '0x0D', '--target-file', '7'),
                                         ('--roster-state',)])
        self.assertEqual(plan.effects['animations'], {7: 'back to the stock one'})

    def test_a_bank_the_slot_loads_already_changes_nothing(self):
        plan = self.plan(0x0D, STOCK[7])
        self.assertTrue(plan.nothing)
        self.assertEqual(plan.commands, [])

    def test_size_and_sharing_notes(self):
        plan = self.plan(0x0D, bank(7, {1, 2, 3}, pad=64), env=AnimEnv(limit=100), st=anim_state(shared=2))
        self.assertTrue(any('bigger than any stock animation bank 7 (100 bytes)' in w for w in plan.warnings))
        self.assertTrue(any('shared by 2 other slots: only this slot changes' in n for n in plan.notes))

    def test_clear_resets_custom_banks(self):
        plan = slot_plan.plan_clear(anim_state({0x0D: [7, 14]}), tsp.make_config(), 0x0D, tsp.STATE_FILE)
        self.assertIn(('--unpatch', '--target-id', '0x0D', '--target-file', '7'), plan.commands)
        self.assertIn(('--unpatch', '--target-id', '0x0D', '--target-file', '14'), plan.commands)
        self.assertEqual(plan.effects['animations'], 'stock')
        self.assertTrue(any('animation banks 7, 14 go back to the stock ones' in n for n in plan.notes))

    def test_a_fresh_directory_copy_warns_about_custom_banks(self):
        plan = slot_plan.plan_patch(anim_state({0x67: [8]}), tsp.make_config(), 0x67,
                                    tsp.pair(tsp.HP, tsp.LOW, 31, 'kinopio'), AnimEnv(), tsp.STATE_FILE,
                                    tsp.NAMES_TEXT)
        self.assertTrue(any('resets its custom animation bank (8)' in w for w in plan.warnings))


class ClassifyAnimTests(unittest.TestCase):
    def test_classification_and_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = os.path.join(tmp, 'walk.anm'), os.path.join(tmp, 'x.anm')
            with open(good, 'wb') as f:
                f.write(bank(13, {1}))
            with open(bad, 'wb') as f:
                f.write(b'\x00' * 64)
            self.assertEqual(slot_plan.classify_anim(good).file, 13)
            with self.assertRaisesRegex(slot_plan.PlanError, 'x.anm: not an animation bank'):
                slot_plan.classify_anim(bad)
            with self.assertRaisesRegex(slot_plan.PlanError, 'cannot be read'):
                slot_plan.classify_anim(os.path.join(tmp, 'missing.anm'))


FILES = {'/a/7.anm': bank(7, {1, 2, 3}), '/b/7.anm': bank(7, {1, 2}, magic=anim.MAGICS[1]),
         '/a/14.anm': bank(14, {1, 2, 3})}


def fake_classify_anim(path):
    if path not in FILES:
        raise slot_plan.PlanError(f'{os.path.basename(path)} cannot be read')
    return pick(FILES[path], path)


class MergeAnimTests(unittest.TestCase):
    def merge(self, *items):
        return slot_plan.merge_edits(tsp.edits(*items), classify_fn=tsp.fake_classify,
                                     classify_anim_fn=fake_classify_anim)

    def test_the_last_edit_per_bank_wins(self):
        merged, notes, refused = self.merge(('anim', '0x0D', None, {'anm': '/a/7.anm'}),
                                            ('anim', '0x0D', None, {'anm': '/a/14.anm'}),
                                            ('anim', '0x0D', None, {'anm': '/b/7.anm'}))
        self.assertEqual(refused, [])
        self.assertEqual([(e.file, e.bank) for e in merged], [('/a/14.anm', 14), ('/b/7.anm', 7)])
        self.assertIn('0x0D: the pending animation bank 7 edit is replaced by the later one', notes)

    def test_a_clear_drops_earlier_banks(self):
        merged, notes, _r = self.merge(('anim', '0x0D', None, {'anm': '/a/7.anm'}), ('clear', '0x0D', None),
                                       ('anim', '0x0D', None, {'anm': '/a/14.anm'}))
        self.assertEqual([e.op for e in merged], ['clear', 'anim'])
        self.assertTrue(any('later clear resets the animations' in n for n in notes))

    def test_refusals(self):
        _m, _n, refused = self.merge(('anim', '0x0D', None, {'anm': '/missing.anm'}),
                                     ('anim', '0x0D', None, {'anm': '/a/7.anm', 'file': 8}))
        self.assertEqual([error for _e, error in refused],
                         ['missing.anm cannot be read', '7.anm is animation bank 7, not 8'])

    def test_parse_and_json(self):
        edit = tsp.edits(('anim', '0x0D', None, {'anm': '/a/7.anm'}))[0]
        self.assertEqual(edit.to_json(), {'op': 'anim', 'id': '0x0D', 'anm': '/a/7.anm'})
        merged, _n, _r = self.merge(('anim', '0x0D', None, {'anm': '/a/7.anm'}))
        self.assertEqual(merged[0].to_json(), {'op': 'anim', 'id': '0x0D', 'anm': '/a/7.anm', 'file': 7})
        for item, message in (({'op': 'anim', 'id': '0x0D'}, 'needs an "anm"'),
                              ({'op': 'anim', 'id': '0x0D', 'anm': '/a/7.anm', 'file': 3}, 'animation bank')):
            with self.assertRaisesRegex(slot_plan.PlanError, message):
                slot_plan.parse_edits([item])

    def test_batch_runs_the_write_in_step_four_and_a_fresh_copy_drops_it(self):
        def batch(*items):
            return slot_plan.plan_batch(anim_state(), tsp.make_config(), tsp.edits(*items), AnimEnv(),
                                        tsp.STATE_FILE, tsp.NAMES_TEXT, classify_fn=tsp.fake_classify,
                                        classify_anim_fn=fake_classify_anim)
        b = batch(('anim', '0x0D', None, {'anm': '/a/7.anm'}), ('rename', '0x0D', None, {'text': 'Toad'}))
        self.assertTrue(b.ok)
        self.assertEqual(b.commands[-2:], [('--write-slot-animation', '0x0D', '7', '/a/7.anm'), ('--roster-state',)])
        b = batch(('anim', '0x66', None, {'anm': '/a/7.anm'}), ('patch', '0x66', tsp.HP))
        self.assertTrue(b.ok)
        self.assertEqual([e.op for e, _p in b.plans], ['patch'])
        self.assertIn('0x66: the animation bank 7 edit is dropped: the later patch copies the model directory afresh',
                      b.notes)


# --------------------------------------------------------------------------
# GUI texts
# --------------------------------------------------------------------------

class GuiTextTests(unittest.TestCase):
    def test_slot_summary_line(self):
        self.assertIsNone(gui_grid.animations_text({}))
        st = anim_state({0x0D: [7, 14]})
        chars = {c['id']: c for c in st['characters']}
        self.assertEqual(gui_grid.animations_text(chars[0x09]), 'Animations: stock')
        self.assertEqual(gui_grid.animations_text(chars[0x0D]), 'Animations: custom (banks 7, 14)')
        chars[0x09]['animations'][0]['vanilla'] = None
        self.assertEqual(gui_grid.animations_text(chars[0x09]), 'Animations: unknown (1_Input could not be read)')
        self.assertIn('Animations: custom (banks 7, 14)', gui_grid.slot_details(st, 0x0D))

    def test_pending_titles_and_dialog(self):
        self.assertEqual(gui_grid._edit_title({'op': 'anim', 'id': '0x0D', 'anm': '/a/7.anm', 'file': 7}),
                         'Pending: animation bank 7 from 7.anm')
        plan = slot_plan.plan_batch(anim_state(), tsp.make_config(), tsp.edits(('anim', '0x0D', None,
                                                                                {'anm': '/a/7.anm'})),
                                    AnimEnv(), tsp.STATE_FILE, tsp.NAMES_TEXT,
                                    classify_anim_fn=fake_classify_anim).to_json()
        st = anim_state()
        dialog = gui_grid.slot_dialog(st, 0x0D, False, plan, 0, '', gui_grid.PendingEdits(), kind='anim',
                                      anm='/a/7.anm')
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Replace an animation bank of Red Toad (0x0D)?')
        texts = [text for text, _kind in dialog.lines]
        self.assertTrue(any(t.startswith('Animation bank 7: 7.anm, 28 animations, all replaced at once') for t in texts))
        self.assertTrue(any("bank checked against the slot's skeleton" in t for t in texts))
        pending = gui_grid.PendingEdits()
        pending.accept(plan)
        self.assertEqual(pending.lines(0x0D), ['Pending: animation bank 7 from 7.anm',
                                               '  Animations: animation bank 7 from 7.anm'])
        summary = gui_grid.summary_dialog(st, plan, 0, '')
        self.assertTrue(any('replace animation bank 7' in text for text, _k in summary.lines))


if __name__ == '__main__':
    unittest.main()
