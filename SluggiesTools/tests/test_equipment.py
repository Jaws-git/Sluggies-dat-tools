"""Equipment slots, bats and gloves (``Roster/gear.py``, ``SlotTarget``, the planner,
the GUI texts, roster packs).

The patcher cases run on the synthetic game of ``test_slot_target`` extended by the equipment files 2-5 (a bat and
two gloves of one bone, each with the 32-byte archive container the game's equipment entries carry; file 5 the empty
placeholder). The planner, GUI and pack cases use hand-made states and fake environments.
"""

import copy
import hashlib
import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.tests import test_slot_target as tst
from SluggiesTools.tests.test_slot_target import OTHER, SOURCE, TARGET, SlotHarness, hh, main, model_block

import LodPartnerGuard  # noqa: E402 (path set up by test_slot_target)
import SlotTarget  # noqa: E402

from SluggiesTools import gui_grid
from SluggiesTools.Roster import gear, pack, slot_plan
from SluggiesTools.tests import test_roster_pack as trp
from SluggiesTools.tests import test_slot_plan as tsp

ARCHIVE_PREFIX = struct.pack('>II', 1, 0x20) + bytes(0x18)           # count 1, member at +0x20 (32 bytes)
PLACEHOLDER = struct.pack('>II', 1, 0x20) + bytes(0x38)              # the shared 64-byte empty file 5


def equipment_block(geo: str, parents=(None,), fill=0) -> bytes:
    return ARCHIVE_PREFIX + model_block(geo, list(parents), fill=fill)


EQUIPMENT = {}
for _chunk, _tag in ((SOURCE, 'r'), (TARGET, 'b'), (OTHER, 'h')):
    EQUIPMENT[(_chunk, 2)] = equipment_block(f'bat_{_tag}.gpl', fill=0x10 + _chunk)
    EQUIPMENT[(_chunk, 3)] = equipment_block(f'glove_l_{_tag}.gpl', fill=0x20 + _chunk)
    EQUIPMENT[(_chunk, 4)] = equipment_block(f'glove_r_{_tag}.gpl', fill=0x30 + _chunk)
    EQUIPMENT[(_chunk, 5)] = PLACEHOLDER
EQUIPMENT[(SOURCE, 5)] = equipment_block('bat_extra_r.gpl', fill=0x55)     # a real file 5, like Peach's
EQUIPMENT[(OTHER, 2)] = equipment_block('bat_h.gpl', parents=(None, 0), fill=0x40)   # two bones: another skeleton
ALL = {**tst.VANILLA, **EQUIPMENT}


class GearTests(unittest.TestCase):
    def test_roles_and_targets(self):
        self.assertEqual(gear.ROLES, {2: 'bat', 3: 'glove_l', 4: 'glove_r', 5: 'extra'})
        self.assertEqual(gear.target_file(2), 2)
        self.assertEqual(gear.target_file(2, 5), 5)           # a bat into the extra slot
        self.assertEqual(gear.target_file(5, 2), 2)           # and the extra bat into the bat slot
        self.assertEqual(gear.target_file(3, 'glove_l'), 3)
        with self.assertRaisesRegex(gear.GearError, 'left glove.*cannot go into file 4'):
            gear.target_file(3, 4)
        with self.assertRaisesRegex(gear.GearError, 'cannot go into file 2'):
            gear.target_file(4, 2)
        with self.assertRaisesRegex(gear.GearError, 'cannot go into file 3'):
            gear.target_file(2, 3)
        with self.assertRaisesRegex(gear.GearError, 'not equipment'):
            gear.target_file(0)

    def test_parse_file(self):
        self.assertEqual([gear.parse_file(v) for v in (2, '3', 'glove_r', ' EXTRA ')], [2, 3, 4, 5])
        for bad in (1, 6, 'hand', '0x10'):
            with self.assertRaises(gear.GearError):
                gear.parse_file(bad)

    def test_placeholder(self):
        self.assertTrue(gear.is_placeholder(PLACEHOLDER))
        self.assertTrue(gear.is_placeholder(bytes(32)))                       # the Mii directories' empty file 5
        self.assertFalse(gear.is_placeholder(EQUIPMENT[(SOURCE, 5)]))
        self.assertFalse(gear.is_placeholder(None))
        self.assertFalse(gear.is_placeholder(PLACEHOLDER[:8] + b'\x01' + PLACEHOLDER[9:]))

    def test_find_gear(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, '22 Peach')

            def write(*parts, chunk=22, file=0):
                path = os.path.join(home, *parts)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump({'SluggiesModel': {'ChunkNumber': chunk, 'FileIndex': file}}, f)
                return path
            model = write('1_peach.gpl', '1_peach.gpl.sluggie')
            write('3_L_peach.gpl', '3_L_peach.gpl.sluggie', file=1)
            bat = write('100', '132_bat.gpl', '132_bat.gpl.sluggie', file=2)
            glove = write('200', '232_glove_L.gpl', '232_glove_L.gpl.sluggie', file=3)
            write('300', '332_glove_R.gpl', '332_glove_R.gpl.sluggie', file=4)
            write('300', '432_glove_R.gpl', '432_glove_R.gpl.sluggie', file=4)          # a second one: ambiguous
            write('400', '532_bat.gpl', '532_bat.gpl.sluggie', file=5, chunk=99)         # another chunk: not ours
            found, ambiguous = gear.find_gear(model, 22)
            self.assertEqual({f: g.path for f, g in found.items()}, {2: bat, 3: glove})
            self.assertEqual(sorted(ambiguous), [4])
            self.assertEqual(len(ambiguous[4]), 2)
            self.assertEqual(gear.find_gear(os.path.join(tmp, 'missing', 'x', 'y.sluggie'), 22), ({}, {}))


class EquipmentHarness(SlotHarness):
    FILES = (0, 1, 2, 3, 4, 5)
    VANILLA = ALL

    def share(self, a, b):
        """Make route ``b`` read the same block as route ``a`` (a shared vanilla bat)."""
        dol = bytearray(self.output_dol.read_bytes())
        words = struct.unpack_from('>12I', dol, self.records[a])
        struct.pack_into('>12I', dol, self.records[b], *words)
        self.output_dol.write_bytes(dol)
        self.vanilla_offsets[b] = self.vanilla_offsets[a]
        self.VANILLA = dict(self.VANILLA)
        self.VANILLA[b] = self.VANILLA[a]

    def target(self, chunk, file, cid=0x13):
        return SlotTarget.Target(chunk, file, cid)


class EquipmentWriteTests(EquipmentHarness):
    def test_a_bat_reaches_the_target_route_only(self):
        block = equipment_block('bat_r.gpl', fill=0x77)
        offset = self.write(block, source=(SOURCE, 2), target=self.target(TARGET, 2))
        self.assertGreaterEqual(offset, tst.BASE_SIZE)
        self.assertEqual(self.route((TARGET, 2)), (offset, len(block)))
        self.assertEqual(self.live((TARGET, 2)), block)
        for route in ((SOURCE, 2), (OTHER, 2), (TARGET, 3), (TARGET, 0), (TARGET, 1)):
            self.assert_vanilla_route(route)

    def test_a_shared_vanilla_bat_stays_for_its_other_users(self):
        self.share((TARGET, 2), (SOURCE, 2))
        shared_offset, shared_length = self.route((TARGET, 2))
        self.assertEqual(hh.findSharedEntries(TARGET, 2), [(SOURCE, 2)])
        block = equipment_block('bat_r.gpl', fill=0x78)
        self.write(block, source=(SOURCE, 2), target=self.target(TARGET, 2))
        self.assertEqual(self.live((TARGET, 2)), block)
        self.assertEqual(self.route((SOURCE, 2)), (shared_offset, shared_length))      # not repointed
        self.assertEqual(self.bytes_at(shared_offset, shared_length), EQUIPMENT[(TARGET, 2)])   # and not zeroed

    def test_the_last_user_of_a_vanilla_block_zeroes_it_like_a_model(self):
        length = len(EQUIPMENT[(TARGET, 3)])
        offset = self.route((TARGET, 3))[0]
        self.write(equipment_block('glove_l_r.gpl', fill=0x79), source=(SOURCE, 3), target=self.target(TARGET, 3))
        self.assertEqual(self.bytes_at(offset, length), bytes(length))

    def test_extra_bat_into_the_empty_slot_and_the_bat_slot_swap(self):
        extra = EQUIPMENT[(SOURCE, 5)]
        self.write(extra, source=(SOURCE, 5), target=self.target(TARGET, 5))
        self.assertEqual(self.live((TARGET, 5)), extra)
        self.assert_vanilla_route((OTHER, 5))                                  # every other empty slot as it was
        bat = equipment_block('bat_r.gpl', fill=0x7A)
        self.write(bat, source=(SOURCE, 2), target=self.target(TARGET, 5))     # a bat into the extra slot
        self.assertEqual(self.live((TARGET, 5)), bat)

    def test_no_lod_checks_apply(self):
        with mock.patch.object(LodPartnerGuard, 'lod_partner_errors') as errors:
            self.write(equipment_block('bat_r.gpl', fill=0x7B), source=(SOURCE, 2), target=self.target(TARGET, 2))
        errors.assert_not_called()

    def test_make_target_by_role(self):
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)):
            self.assertEqual(SlotTarget.make_target('0x13', (SOURCE, 3)), SlotTarget.Target(TARGET, 3, 0x13))
            self.assertTrue(SlotTarget.make_target('0x13', (SOURCE, 3)).equipment)
            self.assertEqual(SlotTarget.make_target('0x13', (SOURCE, 2), target_file=5),
                             SlotTarget.Target(TARGET, 5, 0x13))
            with self.assertRaisesRegex(SlotTarget.TargetError, 'cannot go into file 4'):
                SlotTarget.make_target('0x13', (SOURCE, 3), target_file=4)
            with self.assertRaisesRegex(SlotTarget.TargetError, 'cannot go into file 2'):
                SlotTarget.make_target('0x13', (SOURCE, 4), target_file=2)
            with self.assertRaisesRegex(SlotTarget.TargetError, 'as-low'):
                SlotTarget.make_target('0x13', (SOURCE, 2), as_low=True)
            with self.assertRaisesRegex(SlotTarget.TargetError, 'not equipment'):
                SlotTarget.make_target('0x13', (SOURCE, 0), target_file=2)         # a model into a bat slot
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x12, SOURCE)):
            self.assertIsNone(SlotTarget.make_target('0x12', (SOURCE, 2)))

    def test_role_mismatch_writes_nothing(self):
        before = self.output_dol.read_bytes()
        with self.assertRaisesRegex(SlotTarget.TargetError, 'cannot go into file'):
            SlotTarget.check(EQUIPMENT[(SOURCE, 3)], (SOURCE, 3), SlotTarget.Target(TARGET, 4, 0x13))
        self.assertEqual(self.output_dol.read_bytes(), before)

    def test_skeleton_prefix_rule(self):
        # the target's vanilla bat has one bone; a source with two (an added bone) is fine, one that lacks a bone not
        two_bones = equipment_block('bat_r.gpl', parents=(None, 0))
        self.assertEqual(SlotTarget.check(two_bones, (SOURCE, 2), SlotTarget.Target(TARGET, 2, 0x13)), [])
        with self.assertRaisesRegex(SlotTarget.TargetError, 'skeletons do not match.*1 bones'):
            SlotTarget.check(EQUIPMENT[(SOURCE, 2)], (SOURCE, 2), SlotTarget.Target(OTHER, 2, 0x14))

    def test_an_empty_target_has_no_skeleton_to_match(self):
        three = equipment_block('bat_r.gpl', parents=(None, 0, 1))
        self.assertEqual(SlotTarget.check(three, (SOURCE, 2), SlotTarget.Target(TARGET, 5, 0x13)), [])

    def test_container_rules(self):
        block = EQUIPMENT[(SOURCE, 2)]
        with mock.patch.object(SlotTarget, '_vanilla_summary', return_value=None):
            self.assertEqual(SlotTarget.check(block, (SOURCE, 2), SlotTarget.Target(TARGET, 2, 0x13),
                                              {'container_prefix_size': 0x20, 'archive_populated_slots': 1}), [])
            with self.assertRaisesRegex(SlotTarget.TargetError, 'several members'):
                SlotTarget.check(block, (SOURCE, 2), SlotTarget.Target(TARGET, 2, 0x13),
                                 {'archive_populated_slots': 2})
            with self.assertRaisesRegex(SlotTarget.TargetError, 'not a character directory'):
                SlotTarget.check(block, (9, 2), SlotTarget.Target(TARGET, 2, 0x13))

    def test_inner_block_strips_the_container(self):
        inner = SlotTarget.inner_block(EQUIPMENT[(SOURCE, 2)])
        self.assertEqual(inner, model_block('bat_r.gpl', [None], fill=0x10 + SOURCE))
        self.assertEqual(SlotTarget.inner_block(tst.VANILLA[(SOURCE, 0)]), tst.VANILLA[(SOURCE, 0)])   # no archive


class EquipmentClearTests(EquipmentHarness):
    def test_a_stock_file_returns_to_its_vanilla_route(self):
        self.write(equipment_block('bat_r.gpl', fill=0x7C), source=(SOURCE, 2), target=self.target(TARGET, 2))
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)):
            self.assertTrue(main.ClearEquipment('0x13', 2))
        self.assert_vanilla_route((TARGET, 2))

    def test_the_vanilla_bytes_other_slots_use_are_not_rewritten(self):
        self.share((TARGET, 2), (SOURCE, 2))
        offset, length = self.route((TARGET, 2))
        self.write(equipment_block('bat_r.gpl', fill=0x7D), source=(SOURCE, 2), target=self.target(TARGET, 2))
        marker = b'untangled'                                               # an untangle export changed the bytes
        with open(self.output_dat, 'r+b') as dat:
            dat.seek(offset)
            dat.write(marker)
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)):
            self.assertTrue(main.ClearEquipment('0x13', 2))
        self.assertEqual(self.route((TARGET, 2)), (offset, length))
        self.assertEqual(self.bytes_at(offset, len(marker)), marker)

    def test_an_untouched_file_is_left_alone(self):
        before = (self.output_dol.read_bytes(), self.output_dat.read_bytes())
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)), \
                mock.patch.object(hh, 'removeModelFromHammerspace') as remove:
            self.assertTrue(main.ClearEquipment('0x13', 3))
        remove.assert_not_called()
        self.assertEqual((self.output_dol.read_bytes(), self.output_dat.read_bytes()), before)

    def test_empty_file_5_returns_to_the_placeholder(self):
        self.write(EQUIPMENT[(SOURCE, 5)], source=(SOURCE, 5), target=self.target(TARGET, 5))
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)):
            self.assertTrue(main.ClearEquipment('0x13', 5))
        self.assert_vanilla_route((TARGET, 5))

    def test_bad_arguments_are_refused(self):
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)):
            self.assertFalse(main.ClearEquipment('0x13', 1))
            self.assertFalse(main.ClearEquipment('0x13', 7))

    def test_unpatch_with_a_sluggie_target(self):
        self.write(equipment_block('bat_r.gpl', fill=0x7E), source=(SOURCE, 2), target=self.target(TARGET, 2))
        self.assertTrue(main.UnpatchTarget(self.target(TARGET, 2)))
        self.assert_vanilla_route((TARGET, 2))


class EquipmentOwnDirectoryTests(EquipmentHarness):
    OWN = tst.OWN

    def setUp(self):
        super().setUp()
        dol = bytearray(self.output_dol.read_bytes())
        dat = bytearray(self.output_dat.read_bytes())
        start = len(dol)
        self.copies = {}
        for file_index in self.FILES:
            block = ALL[(SOURCE, file_index)]
            offset = len(dat)
            dat += block + bytes(-len(block) % hh.HS_ALIGN_BYTES)
            self.copies[file_index] = (offset, len(block))
            self.records[(self.OWN, file_index)] = len(dol)
            dol += struct.pack('>12I', *([hh._DAT_FNAME_PTR, len(block), offset, len(block)] * 3))
        dol += b'\x00' * hh._ENTRY_SIZE
        self.output_dol.write_bytes(dol)
        self.output_dat.write_bytes(dat)
        for patcher in (
            mock.patch.object(hh, '_DIRS_COUNT', self.OWN),
            mock.patch.object(hh, '_extraDirStarts', return_value=[start]),
            mock.patch.object(hh, 'ownDirSource', side_effect=lambda chunk: SOURCE if chunk == self.OWN else None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_bat_replaces_the_copy_and_nothing_vanilla_changes(self):
        block = equipment_block('bat_r.gpl', fill=0x66)
        offset = self.write(block, source=(SOURCE, 2), target=self.target(self.OWN, 2, 0x66))
        self.assertEqual(self.route((self.OWN, 2)), (offset, len(block)))
        self.assertEqual(self.route((self.OWN, 3)), self.copies[3])
        for route in ALL:
            self.assert_vanilla_route(route)

    def test_the_skeleton_rule_uses_the_directory_source(self):
        # the directory started from SOURCE's one-bone bat: a block without bones does not fit
        bones0 = equipment_block('bat_r.gpl', parents=())
        with self.assertRaisesRegex(SlotTarget.TargetError, 'skeletons do not match'):
            SlotTarget.check(bones0, (SOURCE, 2), self.target(self.OWN, 2, 0x66))

    def test_clear_writes_a_fresh_copy_of_the_sources_block(self):
        self.write(equipment_block('bat_r.gpl', fill=0x67), source=(SOURCE, 2), target=self.target(self.OWN, 2, 0x66))
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x66, self.OWN)):
            self.assertTrue(main.ClearEquipment('0x66', 2))
            self.assertEqual(self.live((self.OWN, 2)), ALL[(SOURCE, 2)])
            before = (self.output_dol.read_bytes(), self.output_dat.read_bytes())
            self.assertTrue(main.ClearEquipment('0x66', 2))              # at its baseline already: nothing
            self.assertEqual((self.output_dol.read_bytes(), self.output_dat.read_bytes()), before)

    def test_write_slot_equipment_validates_and_routes_one_file(self):
        block = equipment_block('bat_r.gpl', fill=0x68)
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x66, self.OWN)):
            self.assertTrue(main.WriteSlotEquipment('0x66', 3, equipment_block('glove_l_r.gpl', fill=0x69)))
            self.assertTrue(main.WriteSlotEquipment('0x66', 2, block))
            self.assertEqual(self.live((self.OWN, 2)), block)
            self.assertFalse(main.WriteSlotEquipment('0x66', 1, block))                      # not an equipment file
            self.assertFalse(main.WriteSlotEquipment('0x66', 2, equipment_block('x.gpl', parents=())))   # skeleton
        self.assertEqual(self.route((self.OWN, 4)), self.copies[4])


# --------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------

BAT = '/m/27 Bowser/150000000/150000032_bat.gpl/150000032_bat.gpl.sluggie'
GLOVE_L = '/m/27 Bowser/150100000/150100032_glove_L.gpl/150100032_glove_L.gpl.sluggie'
GLOVE_R = '/m/27 Bowser/150200000/150200032_glove_R.gpl/150200032_glove_R.gpl.sluggie'
EXTRA = '/m/27 Bowser/150300000/150300032_bat.gpl/150300032_bat.gpl.sluggie'
GEAR_FILES = {BAT: (27, 2), GLOVE_L: (27, 3), GLOVE_R: (27, 4), EXTRA: (27, 5)}


def fake_classify_gear(path):
    chunk, file = GEAR_FILES[path]
    return slot_plan.GearPick(chunk, file, path)


def equipment_state(flags=None):
    """Make ``tsp.make_state()`` carry equipment: ``flags``: ``{cid: {role: dict(vanilla=..., ...)}}``."""
    flags = flags or {}
    st = tsp.make_state()
    for c in st['characters']:
        c['equipment'] = {}
        for role, file in (('bat', 2), ('glove_l', 3), ('glove_r', 4), ('extra', 5)):
            entry = {'file': file, 'offset': 1000 + file, 'length': 4096, 'sha1': f'{c["id"]:02x}{file}' * 5,
                     'placeholder': file == 5, 'shared_with': 2, 'vanilla': True}
            entry.update((flags.get(c['id']) or {}).get(role, {}))
            c['equipment'][role] = entry
    return st


class GearEnv(tsp.FakeEnv):
    def __init__(self, equipment_errors=(), **kw):
        super().__init__(**kw)
        self.equipment_errors = list(equipment_errors)
        self.equipment_calls = []

    def equipment_problems(self, source, target):
        self.equipment_calls.append((source, target))
        return list(self.equipment_errors)


class PlanEquipTests(unittest.TestCase):
    def plan(self, cid, path=BAT, file=None, env=None, st=None, config=None, origin='user'):
        pick = fake_classify_gear(path)
        return slot_plan.plan_equip(st or equipment_state(), config or tsp.make_config(), cid, pick,
                                    pick.source_file if file is None else file, env or GearEnv(), tsp.STATE_FILE,
                                    origin)

    def test_a_stock_slot_gets_one_validate_and_one_write(self):
        plan = self.plan(0x0D)
        self.assertEqual(plan.commands, [
            ('--patch', BAT, '--target-id', '0x0D', '--validate-only'),
            ('--patch', BAT, '--target-id', '0x0D'),
            ('--roster-state',)])
        self.assertIsNone(plan.config)                                      # no roster rebuild
        self.assertEqual(plan.gear['file'], 2)
        self.assertEqual(plan.gear['shared_with'], 2)
        self.assertTrue(any('shared by 2 other slots: only this slot changes' in n for n in plan.notes))
        self.assertEqual(plan.effects['equipment'], {2: 'bat from 150000032_bat.gpl.sluggie'})
        self.assertEqual(plan.to_json()['gear']['label'], 'Bat')

    def test_the_skeleton_is_checked_against_the_directory_the_files_came_from(self):
        env = GearEnv()
        self.plan(0x0D, env=env)
        self.assertEqual(env.equipment_calls, [((27, 2), (31, 2))])        # Toad's directory, file 2
        env = GearEnv()
        self.plan(0x67, env=env)                                            # a new ID with an own directory of 0x09
        self.assertEqual(env.equipment_calls, [((27, 2), (27, 2))])

    def test_a_skeleton_mismatch_is_refused(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'skeletons do not match.*bat'):
            self.plan(0x0D, env=GearEnv(['2 bones, 1 bone']))

    def test_extra_slot_needs_the_target_file_command_only_when_it_differs(self):
        plan = self.plan(0x0D, path=BAT, file=5)
        self.assertEqual(plan.commands[0], ('--patch', BAT, '--target-id', '0x0D', '--validate-only',
                                            '--target-file', '5'))
        self.assertEqual(plan.commands[1], ('--patch', BAT, '--target-id', '0x0D', '--target-file', '5'))
        self.assertIn('--target-file', plan.commands[1])
        same = self.plan(0x0D, path=EXTRA)                                  # file 5 into its own file
        self.assertNotIn('--target-file', same.commands[1])

    def test_a_new_id_without_a_directory_gets_one_from_its_template(self):
        plan = self.plan(0x66)
        self.assertEqual(plan.commands[1], ('--roster', '--state', tsp.STATE_FILE))
        self.assertEqual(tsp.ids_entry(plan.config, 0x66)['model'], {'from': '0x04'})
        self.assertEqual(plan.commands[2], ('--patch', BAT, '--target-id', '0x66'))
        self.assertFalse(any('shared by' in n for n in plan.notes))        # a new ID's equipment is its own

    def test_a_new_id_keeps_its_own_directory(self):
        plan = self.plan(0x67)
        self.assertIsNone(plan.config)

    def test_a_config_entry_set_by_an_earlier_edit_is_kept(self):
        config = tsp.make_config()
        tsp.ids_entry(config, 0x66)['model'] = {'from': '0x09'}             # the batch's model patch chose Bowser
        env = GearEnv()
        plan = self.plan(0x66, config=config, env=env)
        self.assertIsNone(plan.config)                                      # nothing changes
        self.assertEqual(tsp.ids_entry(config, 0x66)['model'], {'from': '0x09'})
        # the skeleton is checked against the directory the batch makes (Bowser's), not the template's (Peach's)
        self.assertEqual(env.equipment_calls, [((27, 2), (27, 2))])

    def test_unknown_slot(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'not a slot of this roster'):
            self.plan(0x70)

    def test_clear_plan(self):
        env = GearEnv()
        st = equipment_state({0x0D: {'glove_l': {'vanilla': False}}})
        plan = slot_plan.plan_equip_clear(st, tsp.make_config(), 0x0D, 3, env, tsp.STATE_FILE)
        self.assertEqual(plan.commands, [('--unpatch', '--target-id', '0x0D', '--target-file', '3'),
                                         ('--roster-state',)])
        self.assertEqual(plan.effects['equipment'], {3: 'back to vanilla'})
        again = slot_plan.plan_equip_clear(equipment_state(), tsp.make_config(), 0x0D, 3, env, tsp.STATE_FILE)
        self.assertTrue(again.nothing)
        self.assertEqual(again.commands, [])
        new = slot_plan.plan_equip_clear(equipment_state({0x67: {'bat': {'vanilla': False}}}), tsp.make_config(),
                                         0x67, 2, env, tsp.STATE_FILE)
        self.assertIn("its template's", new.notes[0])


class ClassifyGearTests(unittest.TestCase):
    def write(self, tmp, chunk, file, name='x.gpl.sluggie'):
        path = os.path.join(tmp, name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'SluggiesModel': {'ChunkNumber': chunk, 'FileIndex': file}}, f)
        return path

    def test_classification_and_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            pick = slot_plan.classify_gear(self.write(tmp, 22, 5))
            self.assertEqual((pick.chunk, pick.source_file, pick.source), (22, 5, 4))
            with self.assertRaisesRegex(slot_plan.PlanError, 'character model.*model row'):
                slot_plan.classify_gear(self.write(tmp, 22, 0))
            with self.assertRaisesRegex(slot_plan.PlanError, 'not equipment'):
                slot_plan.classify_gear(self.write(tmp, 22, 7))
            with self.assertRaisesRegex(slot_plan.PlanError, 'not a character directory'):
                slot_plan.classify_gear(self.write(tmp, 9, 2))
            with self.assertRaisesRegex(slot_plan.PlanError, 'not a .sluggie'):
                slot_plan.classify_gear(os.path.join(tmp, 'missing.sluggie'))

    def test_a_model_pick_for_the_model_row_points_at_the_equipment_tiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(slot_plan.PlanError, 'bat \\(file 2\\).*Equipment row'):
                slot_plan.classify(self.write(tmp, 27, 2))
            with self.assertRaisesRegex(slot_plan.PlanError, 'left glove'):
                slot_plan.classify(self.write(tmp, 27, 3))


class MergeEquipTests(unittest.TestCase):
    def merge(self, edits, found=None, ambiguous=None):
        def find(path, chunk):
            return (found or {}), (ambiguous or {})
        return slot_plan.merge_edits(edits, tsp_classify, None, fake_classify_gear, find)

    def equip(self, cid, path, file=None, origin='user', index=1):
        return slot_plan.Edit('equip', cid, path, index=index, gear_file=file, origin=origin)

    def test_the_last_edit_per_slot_file_wins(self):
        merged, notes, refused = self.merge([self.equip(0x0D, BAT, index=1), self.equip(0x0D, GLOVE_L, index=2),
                                             self.equip(0x0D, BAT, index=3),
                                             slot_plan.Edit('equip_clear', 0x0D, gear_file=3, index=4)])
        self.assertEqual([(e.op, e.gear_file) for e in merged], [('equip', 2), ('equip_clear', 3)])
        self.assertEqual([e.index for e in merged], [3, 4])
        self.assertEqual(refused, [])
        self.assertEqual(len(notes), 2)

    def test_a_role_mismatch_is_refused_at_merge(self):
        merged, _notes, refused = self.merge([self.equip(0x0D, GLOVE_L, file=4)])
        self.assertEqual(merged, [])
        self.assertIn('cannot go into file 4', refused[0][1])

    def test_a_slot_clear_drops_earlier_equipment_edits_and_keeps_later_ones(self):
        merged, notes, _ = self.merge([self.equip(0x0D, BAT, index=1), slot_plan.Edit('clear', 0x0D, index=2),
                                       self.equip(0x0D, GLOVE_L, index=3)])
        self.assertEqual([(e.op, e.gear_file) for e in merged], [('clear', None), ('equip', 3)])
        self.assertTrue(any('later clear resets the equipment' in n for n in notes))

    def test_other_slots_are_independent(self):
        merged, _, _ = self.merge([self.equip(0x0D, BAT), self.equip(0x09, BAT, index=2)])
        self.assertEqual(len(merged), 2)

    def test_parse_and_json(self):
        edits = slot_plan.parse_edits({'edits': [
            {'op': 'equip', 'id': '0x0D', 'file': 5, 'sluggie': BAT, 'origin': 'bundled'},
            {'op': 'equip', 'id': '0x0D', 'sluggie': GLOVE_L},
            {'op': 'equip_clear', 'id': '0x0D', 'file': 'glove_r'}]})
        self.assertEqual([(e.op, e.gear_file, e.file, e.origin) for e in edits],
                         [('equip', 5, BAT, 'bundled'), ('equip', None, GLOVE_L, 'user'),
                          ('equip_clear', 4, None, 'user')])
        self.assertEqual(edits[0].to_json(), {'op': 'equip', 'id': '0x0D', 'file': 5, 'sluggie': BAT,
                                              'origin': 'bundled'})
        self.assertEqual(edits[2].to_json(), {'op': 'equip_clear', 'id': '0x0D', 'file': 4})
        for bad in ({'op': 'equip', 'id': '0x0D'}, {'op': 'equip_clear', 'id': '0x0D'},
                    {'op': 'equip', 'id': '0x0D', 'sluggie': BAT, 'file': 9},
                    {'op': 'equip', 'id': '0x0D', 'sluggie': BAT, 'origin': 'robot'}):
            with self.assertRaises(slot_plan.PlanError):
                slot_plan.parse_edits([bad])


def tsp_classify(path):
    return tsp.pair(high=path, low=None, chunk=27, stem='koopa')


class BundledGearTests(unittest.TestCase):
    """Bundled gear: a model patch into a new ID takes its gear along."""

    HIGH = tsp.HP
    FOUND = {2: slot_plan.gear.Gear(2, BAT), 3: slot_plan.gear.Gear(3, GLOVE_L)}

    def merge(self, edits, found=None, ambiguous=None, classify=None):
        return slot_plan.merge_edits(edits, classify or (lambda p: tsp.pair()), None, fake_classify_gear,
                                     lambda path, chunk: (self.FOUND if found is None else found, ambiguous or {}))

    def patch(self, cid=0x66, index=1, **kw):
        return slot_plan.Edit('patch', cid, self.HIGH, index=index, **kw)

    def test_found_files_are_staged_behind_the_patch(self):
        merged, notes, refused = self.merge([self.patch()])
        self.assertEqual([(e.op, e.gear_file, e.origin) for e in merged],
                         [('patch', None, None), ('equip', 2, 'bundled'), ('equip', 3, 'bundled')])
        self.assertEqual(refused, [])
        text = ' '.join(notes)
        self.assertIn('bat: 150000032_bat.gpl.sluggie', text)
        self.assertIn('no right glove found', text)
        self.assertIn('no extra bat (file 5) found', text)
        self.assertIn('stay as the slot has them', text)

    def test_stock_slots_are_not_touched(self):
        merged, _, _ = self.merge([self.patch(cid=0x09)])
        self.assertEqual([e.op for e in merged], ['patch'])

    def test_no_gear_switch(self):
        merged, _, _ = self.merge([self.patch(no_gear=True)])
        self.assertEqual([e.op for e in merged], ['patch'])

    def test_a_staged_user_edit_wins_before_or_after(self):
        user = slot_plan.Edit('equip', 0x66, EXTRA, gear_file=2, origin='user', index=1)      # a bat from elsewhere
        merged, notes, _ = self.merge([user, self.patch(index=2)])
        self.assertEqual([(e.op, e.gear_file, e.origin, e.file) for e in merged],
                         [('equip', 2, 'user', EXTRA), ('patch', None, None, tsp.HP),
                          ('equip', 3, 'bundled', GLOVE_L)])
        self.assertIn('the one you staged stays', ' '.join(notes))
        later = slot_plan.Edit('equip', 0x66, EXTRA, gear_file=2, origin='user', index=3)
        merged, _, _ = self.merge([self.patch(), later])
        self.assertEqual([(e.gear_file, e.origin) for e in merged if e.op == 'equip'], [(3, 'bundled'), (2, 'user')])

    def test_a_clear_staged_by_the_user_also_stays(self):
        clear = slot_plan.Edit('equip_clear', 0x66, gear_file=3, index=1)
        merged, _, _ = self.merge([clear, self.patch(index=2)])
        self.assertEqual([(e.op, e.gear_file) for e in merged if e.op != 'patch'], [('equip_clear', 3), ('equip', 2)])

    def test_a_later_patch_replaces_the_gear_of_the_earlier_one(self):
        other = {5: slot_plan.gear.Gear(5, EXTRA)}
        first = self.merge([self.patch()])[0]
        self.assertEqual(len(first), 3)
        results = iter([(self.FOUND, {}), (other, {})])
        merged, notes, _ = slot_plan.merge_edits([self.patch(index=1), self.patch(index=2)], lambda p: tsp.pair(),
                                                 None, fake_classify_gear, lambda path, chunk: next(results))
        self.assertEqual([(e.op, e.gear_file) for e in merged], [('patch', None), ('equip', 5)])
        self.assertTrue(any('gear of the earlier patch is replaced' in n for n in notes))

    def test_ambiguous_gear_refuses_the_patch(self):
        merged, _, refused = self.merge([self.patch()], found={}, ambiguous={4: ['/a/x.sluggie', '/b/y.sluggie']})
        self.assertEqual(merged, [])
        self.assertIn('right glove', refused[0][1])
        self.assertIn('x.sluggie, y.sluggie', refused[0][1])

    def test_a_clear_after_the_patch_resets_the_gear_edits(self):
        merged, notes, _ = self.merge([self.patch(), slot_plan.Edit('clear', 0x66, index=2)])
        self.assertEqual([e.op for e in merged], ['clear'])
        self.assertTrue(any('resets the equipment' in n for n in notes))


class BatchEquipTests(unittest.TestCase):
    def batch(self, edits, st=None, env=None):
        return slot_plan.plan_batch(st or equipment_state(), tsp.make_config(), edits, env or GearEnv(),
                                    tsp.STATE_FILE, tsp.NAMES_TEXT, classify_fn=tsp_classify,
                                    classify_gear_fn=fake_classify_gear, find_gear_fn=lambda p, c: ({}, {}))

    def test_one_chain_validates_first_then_writes_in_staging_order(self):
        batch = self.batch([slot_plan.Edit('equip', 0x0D, BAT, index=1, origin='user'),
                            slot_plan.Edit('equip', 0x0D, GLOVE_R, index=2, origin='user'),
                            slot_plan.Edit('equip_clear', 0x09, gear_file=3, index=3)],
                           st=equipment_state({0x09: {'glove_l': {'vanilla': False}}}))
        self.assertTrue(batch.ok)
        self.assertEqual(batch.commands, [
            ('--patch', BAT, '--target-id', '0x0D', '--validate-only'),
            ('--patch', GLOVE_R, '--target-id', '0x0D', '--validate-only'),
            ('--patch', BAT, '--target-id', '0x0D'),
            ('--patch', GLOVE_R, '--target-id', '0x0D'),
            ('--unpatch', '--target-id', '0x09', '--target-file', '3'),
            ('--roster-state',)])
        self.assertIsNone(batch.config)

    def test_a_model_patch_and_its_gear_share_one_rebuild(self):
        found = {2: slot_plan.gear.Gear(2, BAT)}
        batch = slot_plan.plan_batch(equipment_state(), tsp.make_config(),
                                     [slot_plan.Edit('patch', 0x66, tsp.HP, index=1)], GearEnv(), tsp.STATE_FILE,
                                     tsp.NAMES_TEXT, classify_fn=lambda p: tsp.pair(),
                                     classify_gear_fn=fake_classify_gear, find_gear_fn=lambda p, c: (found, {}))
        self.assertTrue(batch.ok, batch.refused)
        rebuilds = [c for c in batch.commands if c[0] == '--roster']
        self.assertEqual(len(rebuilds), 1)
        writes = [c for c in batch.commands if c[0] == '--patch' and '--validate-only' not in c]
        self.assertEqual([c[1] for c in writes], [tsp.HP, BAT])            # the model first, then its gear
        self.assertEqual(tsp.ids_entry(batch.config, 0x66)['model'], {'from': '0x09'})     # the patch's source wins

    def test_a_refused_equipment_edit_refuses_the_batch(self):
        batch = self.batch([slot_plan.Edit('equip', 0x0D, BAT, index=1)], env=GearEnv(['1 bone vs 2']))
        self.assertFalse(batch.ok)
        self.assertEqual(batch.commands, [])
        self.assertIn('skeletons do not match', batch.refused[0][1])

    def test_a_reset_of_a_vanilla_file_is_skipped(self):
        batch = self.batch([slot_plan.Edit('equip_clear', 0x0D, gear_file=2, index=1)])
        self.assertEqual(batch.commands, [])
        self.assertEqual(len(batch.skipped), 1)

    def test_clear_resets_changed_equipment_of_a_stock_slot(self):
        st = equipment_state({0x0D: {'bat': {'vanilla': False}, 'extra': {'vanilla': False}}})
        plan = slot_plan.plan_clear(st, tsp.make_config(), 0x0D, tsp.STATE_FILE, GearEnv())
        self.assertEqual(plan.commands, [('--unpatch', '--target-id', '0x0D'),
                                         ('--unpatch', '--target-id', '0x0D', '--target-file', '2'),
                                         ('--unpatch', '--target-id', '0x0D', '--target-file', '5'),
                                         ('--roster-state',)])
        self.assertIn('bat', plan.effects['equipment'])

    def test_clear_of_a_new_id_resets_equipment_through_the_directory_copy(self):
        st = equipment_state({0x67: {'bat': {'vanilla': False}}})
        plan = slot_plan.plan_clear(st, tsp.make_config(), 0x67, tsp.STATE_FILE, GearEnv())
        self.assertEqual(tsp.ids_entry(plan.config, 0x67)['model'], {'from': '0x04'})
        self.assertFalse(any('--target-file' in c for c in plan.commands))

    def test_changed_equipment_alone_means_the_slot_is_not_at_its_baseline(self):
        env = slot_cli_env()
        st = equipment_state({0x0D: {'bat': {'vanilla': False}}})
        char = next(c for c in st['characters'] if c['id'] == 0x0D)
        self.assertTrue(env.equipment_at_baseline(next(c for c in equipment_state()['characters'] if c['id'] == 0x0D), 2))
        self.assertFalse(env.equipment_at_baseline(char, 2))
        self.assertFalse(env.equipment_at_baseline({'id': 1}, 2))          # no equipment in the read: not known

    def test_patching_over_a_changed_own_directory_warns_about_the_equipment(self):
        st = equipment_state({0x67: {'bat': {'vanilla': False}}})
        toad = tsp.pair(high='/m/31 Toad/1_kinopio.gpl/1_kinopio.gpl.sluggie', low=None, chunk=31, stem='kinopio')
        plan = slot_plan.plan_patch(st, tsp.make_config(), 0x67, toad, tsp.FakeEnv(), tsp.STATE_FILE, tsp.NAMES_TEXT)
        self.assertTrue(any('resets its equipment (bat)' in w for w in plan.warnings))


class SlotCliStateTests(unittest.TestCase):
    def test_the_planner_reads_the_state_with_the_vanilla_flags(self):
        from SluggiesTools.Roster import derive, slot_cli, state, state_cli
        seen = {}

        def fake_plan_batch(st, config, edits, env, state_file, names, skip_checked=False):
            seen['flags'] = [e.get('vanilla') for c in st['characters'] for e in c['equipment'].values()]
            return slot_plan.Batch(None)
        st = equipment_state()
        for c in st['characters']:
            for e in c['equipment'].values():
                e.pop('vanilla')
        with tempfile.TemporaryDirectory() as tmp,                 mock.patch.object(state_cli, '_open', return_value=(None, None)),                 mock.patch.object(state, 'read_state', return_value=st),                 mock.patch.object(state, 'read_names', return_value={}),                 mock.patch.object(derive, 'derive', return_value=derive.Derived({}, {})),                 mock.patch.object(state_cli, 'add_vanilla_flags',
                                  side_effect=lambda r: [e.update(vanilla=True) for c in r['characters']
                                                         for e in c['equipment'].values()]),                 mock.patch.object(slot_plan, 'plan_batch', side_effect=fake_plan_batch):
            slot_cli.run([], output_dir=os.path.join(tmp, '3_Output_Dat'))   # _gui goes beside it
        self.assertTrue(seen['flags'] and all(f is True for f in seen['flags']))


def slot_cli_env():
    from SluggiesTools.Roster import slot_cli
    return slot_cli.FileEnv(None, None)


# --------------------------------------------------------------------------
# GUI texts
# --------------------------------------------------------------------------

class GuiTileTests(unittest.TestCase):
    def tiles(self, st=None, pending=None, cid=0x0D):
        return {t['file']: t for t in gui_grid.equipment_tiles(st or equipment_state(), cid, pending)}

    def test_states_from_the_read(self):
        st = equipment_state({0x0D: {'glove_l': {'vanilla': False}, 'glove_r': {'vanilla': None}}})
        tiles = self.tiles(st)
        self.assertEqual({f: t['state'] for f, t in tiles.items()},
                         {2: 'Original', 3: 'Modified', 4: 'Original', 5: 'Empty'})
        self.assertEqual({f: t['can_reset'] for f, t in tiles.items()}, {2: False, 3: True, 4: False, 5: False})
        self.assertEqual([t['label'] for t in tiles.values()], ['Bat', 'Left glove', 'Right glove', 'Extra bat'])

    def test_no_equipment_in_the_read_means_no_row(self):
        st = tsp.make_state()
        self.assertEqual(gui_grid.equipment_tiles(st, 0x0D), [])
        self.assertEqual(gui_grid.equipment_tiles({'characters': [{'id': 1, 'equipment': {}}]}, 1), [])

    def test_tooltips(self):
        tiles = self.tiles()
        self.assertTrue(any('also loaded by 2 other slots' in line for line in tiles[2]['tip']))
        self.assertTrue(any('not confirmed' in line for line in tiles[5]['tip']))
        self.assertTrue(any('placeholder' in line for line in tiles[5]['tip']))
        new = {t['file']: t for t in gui_grid.equipment_tiles(equipment_state(), 0x66)}
        self.assertFalse(any('also loaded by' in line for line in new[2]['tip']))     # a new ID's own copy

    def test_pending_overlay(self):
        pending = gui_grid.PendingEdits()
        pending.edits = [{'op': 'equip', 'id': '0x0D', 'file': 2, 'sluggie': BAT, 'origin': 'user'},
                         {'op': 'equip_clear', 'id': '0x0D', 'file': 3}]
        tiles = self.tiles(pending=pending)
        self.assertEqual((tiles[2]['state'], tiles[2]['text']), ('Pending', 'bat from 150000032_bat.gpl.sluggie'))
        self.assertEqual(tiles[3]['text'], 'reset the left glove')
        self.assertTrue(tiles[3]['can_reset'])
        self.assertEqual(tiles[4]['state'], 'Original')
        self.assertTrue(pending.has(0x0D))
        self.assertEqual(pending.equip_edit(0x0D, 3)['op'], 'equip_clear')
        self.assertIsNone(pending.equip_edit(0x0D, 5))
        self.assertEqual(pending.summary(0x0D), ['Pending: bat from 150000032_bat.gpl.sluggie',
                                                 'Pending: reset the left glove'])

    def test_titles(self):
        edit = {'op': 'equip', 'id': '0x66', 'file': 5, 'sluggie': EXTRA, 'origin': 'bundled'}
        self.assertEqual(gui_grid._edit_title(edit),
                         'Pending: extra bat (file 5) from 150300032_bat.gpl.sluggie (bundled with the model)')

    def test_tile_files_match_the_planner(self):
        self.assertEqual([(role, file, label) for role, file, label in gui_grid.EQUIP_TILES],
                         [(gear.ROLES[f], f, gear.LABELS[gear.ROLES[f]].split(' (')[0]) for f in gear.FILES])
        self.assertEqual(set(gui_grid.EQUIP_FIELDS), set(pack.EQUIP_ROLES))
        self.assertEqual(gui_grid.EQUIP_OPS, slot_plan.EQUIP_OPS)


class GuiDialogTests(unittest.TestCase):
    def plan(self, edits, **kw):
        st = kw.pop('st', None) or equipment_state({0x0D: {'glove_l': {'vanilla': False}}})
        batch = slot_plan.plan_batch(st, tsp.make_config(), edits, GearEnv(), tsp.STATE_FILE, tsp.NAMES_TEXT,
                                     classify_fn=tsp_classify, classify_gear_fn=fake_classify_gear,
                                     find_gear_fn=lambda p, c: ({}, {}))
        return st, json.loads(json.dumps(batch.to_json()))

    OUTPUT = ('Slot build check passed | Model: 150000032_bat.gpl.sluggie | Size: 0.01 MB (13312 bytes) | '
              'nothing written')

    def test_equip_dialog(self):
        st, plan = self.plan([slot_plan.Edit('equip', 0x0D, BAT, index=1)])
        dialog = gui_grid.slot_dialog(st, 0x0D, True, plan, 0, self.OUTPUT, kind='equip', gear_file=2)
        self.assertEqual(dialog.title, 'Put a bat into Red Toad (0x0D)?')
        text = '\n'.join(line for line, _kind in dialog.lines)
        self.assertIn('Bat: 150000032_bat.gpl.sluggie, 0.01 MB', text)
        self.assertIn('shared by 2 other slots: only this slot changes', text)
        self.assertIn('slot rules, and the model built and validated', text)
        self.assertTrue(dialog.can_apply)

    def test_the_section_is_the_edits_own_when_others_are_pending(self):
        pending = gui_grid.PendingEdits()
        edits = [slot_plan.Edit('equip', 0x0D, GLOVE_R, index=1, gear_file=4),
                 slot_plan.Edit('equip', 0x0D, BAT, index=2)]
        st, plan = self.plan(edits)
        dialog = gui_grid.slot_dialog(st, 0x0D, True, plan, 0, self.OUTPUT, pending, kind='equip', gear_file=2)
        text = '\n'.join(line for line, _kind in dialog.lines)
        self.assertIn('Bat: 150000032_bat.gpl.sluggie', text)
        self.assertNotIn('Right glove:', text)

    def test_a_reset_of_vanilla_is_nothing_to_reset(self):
        st, plan = self.plan([slot_plan.Edit('equip_clear', 0x0D, gear_file=2, index=1)])
        dialog = gui_grid.slot_dialog(st, 0x0D, False, plan, 0, '', gui_grid.PendingEdits(), kind='equip_clear',
                                      gear_file=2)
        self.assertEqual(dialog.title, 'Red Toad (0x0D): nothing to reset')
        self.assertFalse(dialog.can_apply)

    def test_a_reset_of_a_modified_file(self):
        st, plan = self.plan([slot_plan.Edit('equip_clear', 0x0D, gear_file=3, index=1)])
        dialog = gui_grid.slot_dialog(st, 0x0D, False, plan, 0, '', kind='equip_clear', gear_file=3)
        self.assertEqual(dialog.title, 'Reset the left glove of Red Toad (0x0D)?')
        self.assertTrue(dialog.can_apply)

    def test_refusal(self):
        st = equipment_state()
        batch = slot_plan.plan_batch(st, tsp.make_config(), [slot_plan.Edit('equip', 0x0D, GLOVE_L, index=1,
                                                                            gear_file=4)], GearEnv(), tsp.STATE_FILE,
                                     classify_gear_fn=fake_classify_gear)
        plan = json.loads(json.dumps(batch.to_json()))
        dialog = gui_grid.slot_dialog(st, 0x0D, True, plan, 1, '', kind='equip', gear_file=4)
        self.assertTrue(dialog.title.endswith('refused'))
        self.assertIn('cannot go into file 4', dialog.lines[0][0])
        self.assertFalse(dialog.can_apply)

    def test_summary_and_pending_lines(self):
        st, plan = self.plan([slot_plan.Edit('equip', 0x0D, BAT, index=1),
                              slot_plan.Edit('equip_clear', 0x0D, gear_file=3, index=2)])
        dialog = gui_grid.summary_dialog(st, plan, 0, self.OUTPUT)
        text = '\n'.join(line for line, _kind in dialog.lines)
        self.assertIn('Red Toad (0x0D): put a bat in', text)
        self.assertIn('Red Toad (0x0D): reset the left glove', text)
        pending = gui_grid.PendingEdits()
        pending.accept(plan)
        lines = pending.lines(0x0D)
        self.assertIn('  Equipment: bat from 150000032_bat.gpl.sluggie', lines)
        self.assertIn('  Equipment: back to vanilla', lines)

    def test_equipment_writes_make_the_tab_re_read(self):
        self.assertTrue(gui_grid.chain_writes([('--write-slot-equipment', '0x00', '2', 'b.bin')]))
        self.assertTrue(gui_grid.chain_writes([('--unpatch', '--target-id', '0x00', '--target-file', '2')]))
        self.assertFalse(gui_grid.chain_writes([('--patch', BAT, '--target-id', '0x00', '--validate-only')]))


# --------------------------------------------------------------------------
# Roster packs
# --------------------------------------------------------------------------

class EquipGame(trp.Game):
    """``test_roster_pack.Game`` whose characters carry equipment: ``self.gear[(cid, role)] = bytes`` for a changed
    file (the read flags it ``vanilla: False``); the other files are at their baseline."""

    def __init__(self):
        super().__init__()
        self.gear = {}

    def state(self) -> dict:
        st = super().state()
        for c in st['characters']:
            c['equipment'] = {}
            for role, file in (('bat', 2), ('glove_l', 3), ('glove_r', 4), ('extra', 5)):
                block = self.gear.get((c['id'], role))
                c['equipment'][role] = {'file': file, 'sha1': trp.sha1(block or b'vanilla-%d' % file),
                                        'vanilla': False if block else True, 'placeholder': False, 'shared_with': 0}
        return st

    def pack_files(self) -> dict:
        st = self.state()
        pack.add_fingerprints(st, lambda ref: trp.PIXELS[ref['file']])
        blocks = {**self.blocks, **self.gear}
        return pack.pack_files(st, self.derived(), lambda c, r: blocks[(c['id'], r)],
                               lambda d, f: trp.VANILLA.get((d, f)))


class EquipEnv(trp.FakeEnv):
    def __init__(self, equipment_errors=None):
        super().__init__()
        self.equipment_errors, self.equipment_calls = equipment_errors or {}, []

    def equipment_problems(self, directory, file_index, block):
        self.equipment_calls.append((directory, file_index, block))
        return list(self.equipment_errors.get((directory, file_index), [])), []


class EquipPackCase(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.saved = EquipGame()
        self.path = os.path.join(self.tmp, 'roster' + pack.EXTENSION)

    def save(self):
        pack.write_pack(self.path, self.saved.pack_files())

    def plan(self, game, env=None):
        p = pack.read_pack(self.path)
        return pack.plan_load(p, game.state(), game.fingerprints(), game.derived(), env or EquipEnv(), 'state.json',
                              lambda name: f'load/{name}')


class PackEquipmentTests(EquipPackCase):
    def test_fingerprint_lists_only_changed_files(self):
        self.saved.gear[(0x0D, 'bat')] = b'custom-bat'
        fp = self.saved.fingerprints()
        self.assertEqual(fp['0x0D']['bat'], trp.sha1(b'custom-bat'))
        for role in ('glove_l', 'glove_r', 'extra'):
            self.assertIsNone(fp['0x0D'][role])
        self.assertTrue(all(fp['0x00'][role] is None for role in pack.EQUIP_ROLES))
        self.assertIn('bat', pack.FIELDS)
        self.assertEqual(pack.FIELD_LABELS['glove_l'], 'left glove')

    def test_a_new_id_without_own_directory_has_no_equipment_fingerprint(self):
        self.saved.gear[(0x67, 'bat')] = b'template-bat'                       # the template's: not its own
        self.assertIsNone(self.saved.fingerprints()['0x67']['bat'])

    def test_the_pack_holds_changed_files_only(self):
        self.saved.gear[(0x0D, 'bat')] = b'custom-bat'
        self.saved.gear[(0x66, 'glove_r')] = b'own-glove'
        self.save()
        p = pack.read_pack(self.path)
        self.assertEqual(p.meta['format'], 2)
        self.assertEqual(sorted(p.meta['blocks']['0x0D']), ['bat', 'high'])
        self.assertEqual(p.slot_equipment(0x0D), {'bat': b'custom-bat'})
        self.assertEqual(p.slot_equipment(0x66), {'glove_r': b'own-glove'})
        self.assertEqual(p.slot_models(0x0D), {'high': b'toad-hp-edited'})
        self.assertEqual(pack.block_name(0x0D, 'glove_l'), 'models/0x0D_glove_l.bin')

    def test_a_damaged_equipment_block_is_refused(self):
        self.saved.gear[(0x0D, 'bat')] = b'custom-bat'
        self.save()
        with zipfile_rewrite(self.path, 'models/0x0D_bat.bin', b'other'):
            with self.assertRaisesRegex(pack.PackError, 'does not match its fingerprint'):
                pack.read_pack(self.path)

    def test_an_unchanged_game_gives_nothing_to_do(self):
        self.saved.gear[(0x0D, 'bat')] = b'custom-bat'
        self.save()
        game = EquipGame()
        game.gear[(0x0D, 'bat')] = b'custom-bat'
        plan = self.plan(game)
        self.assertEqual(plan.commands, [])
        self.assertTrue(plan.ok)

    def test_the_pack_block_goes_into_a_stock_slot(self):
        self.saved.gear[(0x0D, 'bat')] = b'custom-bat'
        self.save()
        env = EquipEnv()
        plan = self.plan(self._plain_game(), env)
        self.assertEqual(plan.equipment_writes, [(0x0D, 'bat')])
        self.assertEqual(plan.commands[-2:], [('--write-slot-equipment', '0x0D', '2', 'load/models/0x0D_bat.bin'),
                                              ('--roster-state',)])
        self.assertEqual(env.equipment_calls, [(0x1F, 2, b'custom-bat')])      # Toad's directory, file 2
        self.assertEqual(plan.to_json()['equipment_writes'], [{'id': '0x0D', 'role': 'bat'}])

    def _plain_game(self):
        game = EquipGame()
        game.blocks[(0x0D, 'high')] = b'toad-hp-edited'
        return game

    def test_a_file_the_pack_keeps_at_baseline_is_reset_in_the_game(self):
        self.save()                                                            # the pack: nothing changed
        game = self._plain_game()
        game.gear[(0x0D, 'glove_l')] = b'game-glove'                           # the game: changed
        plan = self.plan(game)
        self.assertEqual(plan.equipment_clears, [(0x0D, 3)])
        self.assertIn(('--unpatch', '--target-id', '0x0D', '--target-file', '3'), plan.commands)
        self.assertEqual(plan.to_json()['equipment_clears'], [{'id': '0x0D', 'file': 3}])

    def test_an_own_directory_with_other_equipment_is_copied_afresh_then_written(self):
        self.saved.gear[(0x66, 'bat')] = b'own-bat'
        self.save()
        game = EquipGame()                                                     # the game's 0x66 has its bat at baseline
        plan = self.plan(game)
        self.assertEqual(plan.kept_dirs, [])
        self.assertEqual(plan.equipment_writes, [(0x66, 'bat')])
        self.assertIsNone(plan.config and plan.config['ids'][0]['model'].get('routes'))
        same = EquipGame()
        same.gear[(0x66, 'bat')] = b'own-bat'
        kept = self.plan(same)
        self.assertEqual(kept.kept_dirs, [0x66])
        self.assertEqual(kept.equipment_writes, [])

    def test_a_refused_equipment_block_refuses_the_load(self):
        self.saved.gear[(0x0D, 'bat')] = b'custom-bat'
        self.save()
        plan = self.plan(self._plain_game(), EquipEnv({(0x1F, 2): ['the skeletons do not match']}))
        self.assertFalse(plan.ok)
        self.assertEqual(plan.refused, [(0x0D, 'the bat: the skeletons do not match')])
        self.assertEqual((plan.equipment_writes, plan.equipment_clears), ([], []))

    def test_equipment_blocks_alone_do_not_ask_for_a_model_check(self):
        self.saved.gear[(0x00, 'glove_r')] = b'custom-glove'
        self.save()
        env = EquipEnv()
        self.plan(self._plain_game(), env)
        self.assertTrue(env.calls)
        self.assertTrue(all(call[0] != 0x12 for call in env.calls))      # Mario's directory (only a glove): no model check

    def test_a_format_1_pack_leaves_equipment_alone(self):
        self.save()
        p = pack.read_pack(self.path)
        fp = {k: {f: v for f, v in d.items() if f not in pack.EQUIP_ROLES} for k, d in p.fingerprints.items()}
        game = self._plain_game()
        game.gear[(0x0D, 'bat')] = b'game-bat'
        diff = pack.diff(fp, game.fingerprints())
        self.assertNotIn('bat', next(d for d in diff if d.cid == 0x0D).fields)
        plan = pack.plan_load(dataclass_replace(p, fingerprints=fp), game.state(), game.fingerprints(),
                              game.derived(), EquipEnv(), 'state.json', lambda n: n)
        self.assertEqual((plan.equipment_writes, plan.equipment_clears), ([], []))
        self.assertEqual(plan.commands, [])
        reference = gui_grid.Reference('loaded x', fp)
        state = game.state()
        pack.add_fingerprints(state, lambda ref: trp.PIXELS[ref['file']])
        self.assertEqual(reference.changed(state, 0x0D), [])                  # no "changed since" from the new keys

    def test_format_numbers(self):
        self.save()
        self.assertEqual(pack.FORMAT, 2)
        for number, ok in ((1, True), (2, True), (3, False)):
            with zipfile_meta(self.path, number):
                if ok:
                    pack.read_pack(self.path)
                else:
                    with self.assertRaisesRegex(pack.PackError, 'format 3'):
                        pack.read_pack(self.path)

    def test_the_gui_labels_every_pack_field(self):
        for field in pack.FIELDS:
            self.assertIn(field, gui_grid.PACK_FIELDS)
            self.assertEqual(gui_grid.PACK_FIELDS[field], pack.FIELD_LABELS[field])


def dataclass_replace(obj, **changes):
    import dataclasses
    return dataclasses.replace(obj, **changes)


class zipfile_rewrite:
    """Replace one member of a zip while the context is open (restored on exit)."""

    def __init__(self, path, name, data):
        self.path, self.name, self.data = path, name, data

    def __enter__(self):
        import shutil
        import zipfile
        self.backup = self.path + '.bak'
        shutil.copyfile(self.path, self.backup)
        with zipfile.ZipFile(self.backup) as src, zipfile.ZipFile(self.path, 'w') as dst:
            for item in src.namelist():
                dst.writestr(item, self.data if item == self.name else src.read(item))

    def __exit__(self, *exc):
        import shutil
        shutil.copyfile(self.backup, self.path)


class zipfile_meta(zipfile_rewrite):
    def __init__(self, path, number):
        super().__init__(path, pack.META_FILE, None)
        self.number = number

    def __enter__(self):
        import zipfile
        with zipfile.ZipFile(self.path) as zf:
            meta = json.loads(zf.read(pack.META_FILE))
        meta['format'] = self.number
        self.data = json.dumps(meta).encode()
        return super().__enter__()


class StateFlagTests(unittest.TestCase):
    """``state_cli.add_vanilla_flags``: route-based for stock directories, byte-based for own ones."""

    def test_flags(self):
        from SluggiesTools.Roster import state_cli
        result = {'characters': [
            {'id': 0x00, 'model_dir': 18, 'own_model_dir': False,
             'equipment': {'bat': {'file': 2, 'offset': 100, 'length': 10, 'sha1': 'a'},
                           'extra': {'file': 5, 'offset': 900, 'length': 10, 'sha1': 'b'}}},
            {'id': 0x66, 'model_dir': 172, 'own_model_dir': True,
             'equipment': {'bat': {'file': 2, 'offset': 700, 'length': 3, 'sha1': hashlib.sha1(b'abc').hexdigest()},
                           'glove_l': {'file': 3, 'offset': 800, 'length': 3, 'sha1': 'zzz'}}}]}
        import HammerspaceHelper as helper
        import LodPartnerGuard as guard
        import UntanglePolicy
        with mock.patch.object(helper, 'readDolEntry', side_effect=lambda c, f: (100, 10) if f == 2 else (1, 1)), \
                mock.patch.object(guard, '_vanilla_block', side_effect=lambda c, f: b'abc' if f == 2 else b'xyz'), \
                mock.patch.object(UntanglePolicy, 'is_split', return_value=False):
            state_cli.add_vanilla_flags(result)
        flags = {(c['id'], role): e['vanilla'] for c in result['characters'] for role, e in c['equipment'].items()}
        self.assertEqual(flags, {(0x00, 'bat'): True, (0x00, 'extra'): False, (0x66, 'bat'): True,
                                 (0x66, 'glove_l'): False})
        with mock.patch.object(helper, 'readDolEntry', return_value=(100, 10)), \
                mock.patch.object(UntanglePolicy, 'is_split', return_value=True):
            state_cli.add_vanilla_flags(result)
        self.assertIsNone(result['characters'][0]['equipment']['bat']['vanilla'])        # split copies: not known


if __name__ == '__main__':
    unittest.main()
