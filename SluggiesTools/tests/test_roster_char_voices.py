"""Character voices (``Roster/char_voices.py``): per-ID clip tables behind the ported voice hooks.

Synthetic DOLs as in ``test_roster_voice_stats_reassign`` (species s: clip row ``0x1000 * (s + 1) + slot``;
every inventory site holds its stock word, the hook sites included).
"""

import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Dol import dolfile, inventory, ppc
from SluggiesTools.Roster import char_voices, manifest, pack, slot_cli, slot_plan, state, voices
from SluggiesTools.tests.test_roster_voice_stats_reassign import (NAMES, STATE_FILE, batch, build, char, make_config,
                                                                   make_state)

STEPS = ('dol_hammerspace', 'ids', 'wheels', 'voices', 'grid', 'char_voices')
HEADS = bytes(range(voices.SPECIES))


def clip(s, slot):
    return 0x1000 * (s + 1) + slot


def row_of(s):
    return struct.pack('>12I', *(clip(s, k) for k in range(12)))


class ParseTests(unittest.TestCase):
    def test_entries(self):
        got = char_voices.parse({'character_voices': [{'id': '0x66', 'voice': '0x09'}, {'id': 0, 'voice': 13}]}, HEADS)
        self.assertEqual(got, {0x66: 0x09, 0x00: 0x0D})
        self.assertEqual(char_voices.parse({}, HEADS), {})

    def test_refusals(self):
        for bad in ([{'id': '0x66'}], [{'voice': '0x09'}], [{'id': '0x66', 'voice': '0x47'}],
                    [{'id': '0x100', 'voice': '0x00'}], [{'id': '0x66', 'voice': '0x00'}, {'id': 102, 'voice': 9}],
                    {'id': '0x66', 'voice': '0x00'}):
            with self.subTest(bad=bad), self.assertRaises(char_voices.CharVoiceConfigError):
                char_voices.parse({'character_voices': bad}, HEADS)

    def test_table_replaces_the_name_call_only(self):
        table = char_voices.table_for(row_of(0), row_of(9))
        self.assertEqual(struct.unpack('>12I', table), tuple(clip(9 if k == 10 else 0, k) for k in range(12)))
        with self.assertRaises(ValueError):
            char_voices.table_for(row_of(0)[:8], row_of(9))

    def test_inventory_group(self):
        sites = {s.address: s.stock for s in inventory.group(char_voices.GROUP)}
        self.assertEqual(sites[char_voices.WRAPPER], 0x9421FFC0)
        self.assertEqual(sites[char_voices.LOOKUP], 0x7CA5002E)
        self.assertEqual(ppc.branch_target(sites[char_voices.OWNER_CALL], char_voices.OWNER_CALL), char_voices.SEARCH)
        self.assertEqual(ppc.branch_target(sites[char_voices.RESOLVER_CALL], char_voices.RESOLVER_CALL),
                         char_voices.RESOLVER)
        self.assertEqual([sites[a] for a in char_voices.CAPTURES], [char_voices.CAPTURE_WORD] * 5)
        self.assertEqual(len(sites), 9)


CONFIG = {
    'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None}, {'id': '0x67', 'template': '0x00', 'wheel': '0x00'}],
    'wheels': [{'id': '0x47', 'wheel': '0x06'}],
    'stock_voices': [{'id': '0x00', 'voice': '0x0D'}],
    'grid': {'squares': [{'members': ['0x66'], 'voice': '0x09'}], 'shape': [12, 5]},
    'character_voices': [{'id': '0x66', 'voice': '0x0D'}, {'id': '0x00', 'voice': '0x09'},
                         {'id': '0x67', 'voice': '0x09'}, {'id': '0x47', 'voice': '0x00'}],
}


def table_of(image, cid):
    """The ID's table through the pointer table the wrapper stub reads (its ``load_addr`` pair)."""
    stub = ppc.branch_target(image.u32(char_voices.WRAPPER), char_voices.WRAPPER)
    words = [image.u32(stub + 4 * i) for i in range(80)]
    # the pointer table's address is the last lis/addi r10 pair before the lwzx r11,r10,r11
    lwzx = words.index(ppc.one(stub, lambda a: a.lwzx('r11', 'r10', 'r11')))
    lis, addi = words[lwzx - 2], words[lwzx - 1]
    pointers = ((lis & 0xFFFF) << 16) + ppc.signed16(addi)
    at = image.u32(pointers + 4 * cid)
    return None if at == 0 else image.read(at, 0x30)


class StepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.ctx = build(CONFIG, STEPS)
        cls.plain, _ = build({k: v for k, v in CONFIG.items() if k != 'character_voices'}, STEPS)

    def test_tables_take_the_species_row_and_the_voices_name_call(self):
        # 0x66: square-only on a Bowser-voiced square -> species 9; Toad's stock name call
        self.assertEqual(table_of(self.image, 0x66), char_voices.table_for(row_of(9), row_of(0x0D)))
        # 0x00: Mario's species speaks with Toad's row after the swap; Bowser's stock name call
        self.assertEqual(table_of(self.image, 0x00), char_voices.table_for(row_of(0x0D), row_of(9)))
        self.assertEqual(table_of(self.image, 0x67), char_voices.table_for(row_of(0x0D), row_of(9)))   # on Mario's wheel
        self.assertEqual(table_of(self.image, 0x47), char_voices.table_for(row_of(6), row_of(0)))      # a spare row
        for cid in (0x01, 0x09, 0x0D, 0x46, 0x65):
            self.assertIsNone(table_of(self.image, cid), f'0x{cid:02X}')
        self.assertEqual(self.ctx.state['char_voices'], {0x66: 0x0D, 0x00: 0x09, 0x67: 0x09, 0x47: 0x00})

    def test_hooks_land_at_every_site(self):
        self.assertTrue(char_voices.installed(self.image))
        self.assertFalse(char_voices.installed(self.plain))
        for s in inventory.group(char_voices.GROUP):
            word = self.image.u32(s.address)
            target = ppc.branch_target(word, s.address)
            self.assertIsNotNone(target, f'0x{s.address:08X}')
            self.assertTrue(0x807B7000 <= target < 0x807C0000, f'0x{s.address:08X} -> 0x{target:08X}')
            self.assertEqual(word & 1, 0 if s.address in (char_voices.WRAPPER, char_voices.LOOKUP) else 1)
            self.assertEqual(self.plain.u32(s.address), s.stock)
        wrapper = ppc.branch_target(self.image.u32(char_voices.WRAPPER), char_voices.WRAPPER)
        self.assertEqual(self.image.u32(wrapper), 0x9421FFE0)                    # stwu r1,-0x20(r1)
        lookup = ppc.branch_target(self.image.u32(char_voices.LOOKUP), char_voices.LOOKUP)
        words = [self.image.u32(lookup + 4 * i) for i in range(12)]
        self.assertIn(0x7CA5002E, words)                                         # the displaced lwzx
        self.assertEqual(ppc.branch_target(words[words.index(0x7CA5002E) + 1], lookup + 4 * (words.index(0x7CA5002E) + 1)),
                         char_voices.LOOKUP + 4)
        capture = ppc.branch_target(self.image.u32(char_voices.CAPTURES[0]), char_voices.CAPTURES[0])
        self.assertEqual(self.image.u32(capture), char_voices.CAPTURE_WORD)
        self.assertEqual({ppc.branch_target(self.image.u32(a), a) for a in char_voices.CAPTURES}, {capture})

    def test_species_tables_untouched(self):
        for s in range(voices.SPECIES + 2):
            self.assertEqual(voices.clip_row(self.image, s), voices.clip_row(self.plain, s), s)

    def test_state_manifest_and_fingerprint(self):
        self.assertEqual(state._manifest(self.image)['character_voices'], [[0x00, 0x09], [0x47, 0x00], [0x66, 0x0D],
                                                                          [0x67, 0x09]])
        result = state.read_state(self.image)
        self.assertEqual(result['warnings'], [])
        chars = {c['id']: c for c in result['characters']}
        self.assertEqual((chars[0x66]['voice'], chars[0x66]['char_voice']), (0x0D, 0x0D))
        self.assertEqual((chars[0x00]['voice'], chars[0x00]['char_voice']), (0x09, 0x09))
        self.assertEqual((chars[0x01]['voice'], chars[0x01]['char_voice']), (0x01, None))
        self.assertEqual((chars[0x09]['voice'], chars[0x09]['char_voice']), (0x09, None))
        self.assertEqual((chars[0x0D]['voice'], chars[0x0D]['char_voice']), (0x0D, None))
        plain = {c['id']: c for c in state.read_state(self.plain)['characters']}
        self.assertEqual((plain[0x66]['voice'], plain[0x66]['char_voice']), (0x09, None))
        self.assertEqual((plain[0x00]['voice'], plain[0x00]['char_voice']), (0x0D, None))   # the swap
        fp = {c['id']: pack.fingerprint(result, c, lambda ref: None) for c in result['characters']}
        self.assertEqual((fp[0x66]['char_voice'], fp[0x66]['voice'], fp[0x01]['char_voice']), ('0x0D', '0x09', None))
        self.assertNotIn('character_voices', manifest.build(self.plain and {}, {}))

    def test_without_entries_nothing_changes(self):
        empty, ctx = build({**CONFIG, 'character_voices': []}, STEPS)
        self.assertEqual(empty.to_bytes(), self.plain.to_bytes())
        self.assertNotIn('character_voices', manifest.build(ctx.state, ctx.config))

    def test_refusals(self):
        for entry, text in (({'id': '0x50', 'voice': '0x00'}, 'voice bank'),       # a Mii: no bank
                            ({'id': '0x70', 'voice': '0x00'}, 'not selectable'),   # no ids entry
                            ({'id': '0x48', 'voice': '0x00'}, 'not selectable')):  # a spare row on no wheel
            with self.subTest(entry=entry), self.assertRaises(char_voices.CharVoiceConfigError) as err:
                build({**CONFIG, 'character_voices': [entry]}, STEPS)
            self.assertIn(text, str(err.exception))

    def test_install_refuses_patched_sites(self):
        with self.assertRaises(dolfile.DolError):
            char_voices.install(self.image, self.ctx.state['hammerspace'], {0x00: row_of(0)})


# --------------------------------------------------------------------------
# Planner, CLI
# --------------------------------------------------------------------------

class PlanTests(unittest.TestCase):
    def test_stock_slot_by_family(self):
        plan = slot_plan.plan_char_voice(make_state(), make_config(), 0x0D, 0x0E, STATE_FILE, NAMES)   # Blue Toad -> Toad
        self.assertEqual(plan.config['character_voices'], [{'id': '0x0D', 'voice': '0x0D'}])
        plan = slot_plan.plan_char_voice(make_state(), make_config(), 0x0E, 0x09, STATE_FILE, NAMES)
        self.assertEqual((plan.action, plan.target), ('char_voice', 0x0E))
        self.assertEqual(plan.config['character_voices'], [{'id': '0x0E', 'voice': '0x09'}])
        self.assertEqual(plan.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        self.assertEqual(plan.effects['char_voice'], 'Bowser (0x09)')
        self.assertIn('name call', plan.notes[-1])
        back = slot_plan.plan_char_voice(make_state(), plan.config, 0x0E, None, STATE_FILE, NAMES)
        self.assertNotIn('character_voices', back.config)
        self.assertEqual(back.effects['char_voice'], 'the square\'s')
        self.assertTrue(slot_plan.plan_char_voice(make_state(), make_config(), 0x0E, None, STATE_FILE, NAMES).nothing)
        self.assertTrue(slot_plan.plan_char_voice(make_state(), plan.config, 0x0E, 0x09, STATE_FILE, NAMES).nothing)

    def test_new_id_and_spare_row(self):
        plan = slot_plan.plan_char_voice(make_state(), make_config(), 0x66, 0x0D, STATE_FILE, NAMES)
        self.assertEqual(plan.config['character_voices'], [{'id': '0x66', 'voice': '0x0D'}])
        plan = slot_plan.plan_char_voice(make_state(), plan.config, 0x47, 0x00, STATE_FILE, NAMES)
        self.assertEqual(plan.config['character_voices'], [{'id': '0x47', 'voice': '0x00'}, {'id': '0x66', 'voice': '0x0D'}])

    def test_refusals(self):
        for cid, source in ((0x0D, 0x47), (0x0D, 0x99), (0x99, 0x00)):
            with self.subTest(cid=cid, source=source), self.assertRaises(slot_plan.PlanError):
                slot_plan.plan_char_voice(make_state(), make_config(), cid, source, STATE_FILE, NAMES)
        st = make_state()
        st['characters'].append(char(0x50, 0, name='Mii'))
        with self.assertRaises(slot_plan.PlanError) as err:
            slot_plan.plan_char_voice(st, make_config(), 0x50, 0x00, STATE_FILE, NAMES)
        self.assertIn('Mii', str(err.exception))

    def test_batch_last_per_slot_wins_and_clear_resets(self):
        b = batch(('char_voice', '0x0D', '0x09'), ('char_voice', '0x0D', '0x00'), ('voice', '0x0D', '0x09'),
                  ('char_voice', '0x66', '0x09'))
        self.assertTrue(b.ok, b.refused)
        self.assertEqual(b.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        self.assertEqual(b.config['character_voices'], [{'id': '0x0D', 'voice': '0x00'}, {'id': '0x66', 'voice': '0x09'}])
        self.assertEqual(b.config['stock_voices'], [{'id': '0x0D', 'voice': '0x09'}])
        self.assertEqual([(e.op, e.cid) for e, _p in b.plans], [('char_voice', 0x0D), ('voice', 0x0D), ('char_voice', 0x66)])
        merged, notes, _ = slot_plan.merge_edits(slot_plan.parse_edits([
            {'op': 'char_voice', 'id': '0x66', 'source': '0x09'}, {'op': 'clear', 'id': '0x66'}]))
        self.assertEqual([e.op for e in merged], ['clear'])
        self.assertTrue(any('own voice' in n for n in notes))
        config = make_config()
        config['character_voices'] = [{'id': '0x0D', 'voice': '0x09'}, {'id': '0x66', 'voice': '0x09'}]
        plan = slot_plan.plan_clear(make_state(), config, 0x0D, STATE_FILE)
        self.assertEqual(plan.config['character_voices'], [{'id': '0x66', 'voice': '0x09'}])
        self.assertEqual(plan.effects['char_voice'], 'the square\'s')
        plan = slot_plan.plan_clear(make_state(), plan.config, 0x66, STATE_FILE)
        self.assertNotIn('character_voices', plan.config)

    def test_edit_json(self):
        e = slot_plan.parse_edits([{'op': 'char_voice', 'id': '0x0D', 'source': '0x09'}])
        self.assertEqual([x.to_json() for x in e], [{'op': 'char_voice', 'id': '0x0D', 'source': '0x09'}])
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.parse_edits([{'op': 'char_voice', 'id': '0x0D'}])


class CliTests(unittest.TestCase):
    def test_batch_of_one(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(slot_cli, 'run', return_value=slot_plan.Batch(None)) as run:
            self.assertEqual(slot_cli.main(['--char-voice', '0x66', '0x09', '--output-dir', os.path.join(tmp, 'out')]), 0)
            self.assertEqual(slot_cli.main(['--char-voice', '0x66', '-', '--output-dir', os.path.join(tmp, 'out')]), 0)
        self.assertEqual([(e.op, e.cid, e.source) for e in run.call_args_list[0].args[0]], [('char_voice', 0x66, 0x09)])
        self.assertEqual([(e.op, e.source) for e in run.call_args_list[1].args[0]], [('char_voice', None)])

    def test_dispatcher(self):
        import start
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = os.path.join(tmp, 'plan.json')
            with open(plan_file, 'w') as f:
                json.dump({'commands': [['--roster', '--state', 's.json'], ['--roster-state']]}, f)
            results = [mock.Mock(returncode=0) for _ in range(6)]
            with mock.patch.object(start, 'SLOT_PLAN_FILE', plan_file), \
                    mock.patch('start.subprocess.run', side_effect=results) as run:
                self.assertTrue(start.run_slot_chain('0x66', char_voice='0x09'))
        planners = [c.args[0] for c in run.call_args_list if '--char-voice' in c.args[0]]
        self.assertEqual(planners[0][-3:], ['--char-voice', '0x66', '0x09'])


if __name__ == '__main__':
    unittest.main()
