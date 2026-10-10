"""Character voices (``Roster/char_voices.py``): per-ID clip tables behind the ported voice hooks, and the voice
clips copied into the family files of the sound archive.

Synthetic DOLs as in ``test_roster_voice_stats_reassign`` (species s: clip row ``12 * s + slot``; every inventory
site holds its stock word, the hook sites included) and the synthetic voice archive of ``synthetic_sound``
(``build_voice_archive``: one family file per species, ``file == species``, the name calls in a shared select
file; the same INFO IDs).
"""

import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = pathlib.Path(__file__).resolve().parent
for _p in (TESTS_DIR.parent, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from SluggiesTools.Dol import dolfile, inventory, ppc, relocate
from SluggiesTools.Roster import char_voices, layout_file, manifest, pack, runner, slot_cli, slot_plan, state, steps, voices
from SluggiesTools.Sound import brsar, brsar_write
from SluggiesTools.tests.test_roster_grid import image_for_grid
from SluggiesTools.tests.test_roster_voice_stats_reassign import (NAMES, STATE_FILE, batch, char, fill_voice_tables,
                                                                   make_config, make_state)
from SluggiesTools.tests.test_roster_wheels import FakeLayout
import synthetic_sound as synth

STEPS = ('dol_hammerspace', 'ids', 'wheels', 'voices', 'grid', 'char_voices')
HEADS = bytes(range(voices.SPECIES))
_ARCHIVE = None


def clip(s, slot):
    return synth.voice_id(s, slot)


def row_of(s):
    return struct.pack('>12I', *(clip(s, k) for k in range(12)))


def archive_bytes():
    global _ARCHIVE
    if _ARCHIVE is None:
        _ARCHIVE = synth.build_voice_archive()[0]
    return _ARCHIVE


def build(config, keys=STEPS, sound=True):
    """The roster steps on the synthetic DOL with the synthetic voice archive (``sound=False``: none)."""
    image = image_for_grid()
    fill_voice_tables(image)
    ctx = steps.RosterContext(dol=image, dat=None, config=config, sound=archive_bytes() if sound else None)
    ctx.state[layout_file.STATE_KEY] = FakeLayout()
    with mock.patch.object(relocate, 'scan_refs', return_value=[]):
        for step in steps.all_steps():
            if step.key in keys:
                step.apply(ctx)
        runner.write_manifest(ctx)
    return ctx.dol, ctx


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
    'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None}, {'id': '0x67', 'template': '0x00', 'wheel': '0x00'},
            {'id': '0x68', 'template': '0x00', 'wheel': '0x00'}],
    'wheels': [{'id': '0x47', 'wheel': '0x06'}],
    'stock_voices': [{'id': '0x00', 'voice': '0x0D'}],
    'grid': {'squares': [{'members': ['0x66'], 'voice': '0x09'}], 'shape': [12, 5]},
    'character_voices': [{'id': '0x66', 'voice': '0x0D'},     # species 9 (Bowser's square voice) -> Toad's clips
                         {'id': '0x00', 'voice': '0x09'},     # Mario, whose species speaks with Toad's row -> Bowser's
                         {'id': '0x67', 'voice': '0x09'},     # on Mario's wheel: the same copies as 0x00
                         {'id': '0x68', 'voice': '0x0D'},     # on Mario's wheel, Toad's voice: its row already
                         {'id': '0x47', 'voice': '0x00'}],    # a spare row on Yoshi's wheel -> Mario's clips
}


def table_of(image, cid):
    """The ID's table through the pointer table the wrapper stub reads (its ``load_addr`` pair)."""
    stub = ppc.branch_target(image.u32(char_voices.WRAPPER), char_voices.WRAPPER)
    words = [image.u32(stub + 4 * i) for i in range(80)]
    lwzx = words.index(ppc.one(stub, lambda a: a.lwzx('r11', 'r10', 'r11')))
    lis, addi = words[lwzx - 2], words[lwzx - 1]
    pointers = ((lis & 0xFFFF) << 16) + ppc.signed16(addi)
    at = image.u32(pointers + 4 * cid)
    return None if at == 0 else list(struct.unpack('>12I', image.read(at, 0x30)))


class StepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.ctx = build(CONFIG)
        cls.plain, _ = build({k: v for k, v in CONFIG.items() if k != 'character_voices'})
        cls.out = brsar.Brsar(cls.ctx.state['sound_out'])
        cls.vanilla = brsar.Brsar(archive_bytes())

    def copied(self, family, vs):
        """The INFO IDs of voice species ``vs``' gameplay clips copied into family file ``family``."""
        names = {s.name: s.index for s in self.out.sounds()}
        return [names[char_voices.copy_name(f'sp{vs:02d}_v{k}', family)] for k in char_voices.GAMEPLAY_SLOTS]

    def test_tables_point_at_the_copies_and_the_stock_name_call(self):
        # Mario's species speaks with Toad's row after the swap, so its family file is Toad's (13)
        bowser_in_toad = self.copied(13, 9)
        expect = bowser_in_toad[:10] + [clip(9, 10)] + bowser_in_toad[10:]
        self.assertEqual(table_of(self.image, 0x00), expect)
        self.assertEqual(table_of(self.image, 0x67), expect)                 # the same copies, once
        toad_in_bowser = self.copied(9, 0x0D)
        self.assertEqual(table_of(self.image, 0x66), toad_in_bowser[:10] + [clip(0x0D, 10)] + toad_in_bowser[10:])
        mario_in_yoshi = self.copied(6, 0)
        self.assertEqual(table_of(self.image, 0x47), mario_in_yoshi[:10] + [clip(0, 10)] + mario_in_yoshi[10:])
        self.assertEqual(table_of(self.image, 0x68), [clip(0x0D, k) for k in range(12)])   # its row, no copies
        for cid in (0x01, 0x09, 0x0D, 0x46, 0x65):
            self.assertIsNone(table_of(self.image, cid), f'0x{cid:02X}')
        self.assertEqual(self.ctx.state['char_voices'], {0x66: 0x0D, 0x00: 0x09, 0x67: 0x09, 0x68: 0x0D, 0x47: 0x00})

    def test_archive_copies(self):
        self.assertEqual(len(self.out.sounds()), len(self.vanilla.sounds()) + 33)      # 3 families x 11 clips
        new = self.out.sounds()[len(self.vanilla.sounds()):]
        self.assertEqual(sorted({s.file_id for s in new}), [6, 9, 13])
        for s in new:
            source = next(v for v in self.vanilla.sounds() if char_voices.copy_name(v.name, s.file_id) == s.name)
            self.assertEqual(brsar_write.sound_rwav(self.out, s), brsar_write.sound_rwav(self.vanilla, source))
            self.assertEqual((s.volume, s.player_id), (source.volume, 0))
        for fid in range(len(self.vanilla.files())):
            if fid not in (6, 9, 13):
                self.assertEqual(self.out.file_bytes(fid), self.vanilla.file_bytes(fid), fid)
        for fid in (6, 9, 13):
            self.assertEqual(brsar.rwar_count(self.out.file_bytes(fid)[1]), 11 + 11)
        families = char_voices.family_files(self.image, self.out)
        self.assertEqual(families[0][0], 13)                                 # Mario's species loads Toad's file
        self.assertGreater(families[0x0D][1], char_voices.family_files(self.plain, self.vanilla)[0x0D][1])

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
        k = words.index(0x7CA5002E) + 1
        self.assertEqual(ppc.branch_target(words[k], lookup + 4 * k), char_voices.LOOKUP + 4)
        capture = ppc.branch_target(self.image.u32(char_voices.CAPTURES[0]), char_voices.CAPTURES[0])
        self.assertEqual(self.image.u32(capture), char_voices.CAPTURE_WORD)
        self.assertEqual({ppc.branch_target(self.image.u32(a), a) for a in char_voices.CAPTURES}, {capture})

    def test_species_tables_untouched(self):
        for s in range(voices.SPECIES + 2):
            self.assertEqual(voices.clip_row(self.image, s), voices.clip_row(self.plain, s), s)

    def test_state_manifest_and_fingerprint(self):
        self.assertEqual(state._manifest(self.image)['character_voices'],
                         [[0x00, 0x09], [0x47, 0x00], [0x66, 0x0D], [0x67, 0x09], [0x68, 0x0D]])
        result = state.read_state(self.image)
        self.assertEqual(result['warnings'], [])
        chars = {c['id']: c for c in result['characters']}
        self.assertEqual((chars[0x66]['voice'], chars[0x66]['char_voice']), (0x0D, 0x0D))
        self.assertEqual((chars[0x00]['voice'], chars[0x00]['char_voice']), (0x09, 0x09))
        self.assertEqual((chars[0x01]['voice'], chars[0x01]['char_voice']), (0x01, None))
        self.assertEqual((chars[0x0D]['voice'], chars[0x0D]['char_voice']), (0x0D, None))
        plain = {c['id']: c for c in state.read_state(self.plain)['characters']}
        self.assertEqual((plain[0x66]['voice'], plain[0x66]['char_voice']), (0x09, None))
        self.assertEqual((plain[0x00]['voice'], plain[0x00]['char_voice']), (0x0D, None))   # the swap
        fp = {c['id']: pack.fingerprint(result, c, lambda ref: None) for c in result['characters']}
        self.assertEqual((fp[0x66]['char_voice'], fp[0x66]['voice'], fp[0x01]['char_voice']), ('0x0D', '0x09', None))

    def test_rebuild_is_deterministic(self):
        shuffled = dict(CONFIG, character_voices=list(reversed(CONFIG['character_voices'])))
        image, ctx = build(shuffled)
        self.assertEqual(image.to_bytes(), self.image.to_bytes())
        self.assertEqual(ctx.state['sound_out'], self.ctx.state['sound_out'])

    def test_without_entries_nothing_changes(self):
        empty, ctx = build({**CONFIG, 'character_voices': []})
        self.assertEqual(empty.to_bytes(), self.plain.to_bytes())
        self.assertNotIn('character_voices', manifest.build(ctx.state, ctx.config))
        self.assertNotIn('sound_out', ctx.state)

    def test_without_an_archive(self):
        same_voice = {**CONFIG, 'character_voices': [{'id': '0x68', 'voice': '0x0D'}]}
        image, ctx = build(same_voice, sound=False)                           # no copies needed: fine
        self.assertEqual(table_of(image, 0x68), [clip(0x0D, k) for k in range(12)])
        self.assertNotIn('sound_out', ctx.state)
        with self.assertRaises(char_voices.CharVoiceConfigError) as err:
            build({**CONFIG, 'character_voices': [{'id': '0x00', 'voice': '0x09'}]}, sound=False)
        self.assertIn('1_Input/sound_NA', str(err.exception))

    def test_refusals(self):
        for entry, text in (({'id': '0x50', 'voice': '0x00'}, 'voice bank'),       # a Mii: no bank
                            ({'id': '0x70', 'voice': '0x00'}, 'not selectable'),   # no ids entry
                            ({'id': '0x48', 'voice': '0x00'}, 'not selectable')):  # a spare row on no wheel
            with self.subTest(entry=entry), self.assertRaises(char_voices.CharVoiceConfigError) as err:
                build({**CONFIG, 'character_voices': [entry]})
            self.assertIn(text, str(err.exception))

    def test_install_refuses_patched_sites(self):
        with self.assertRaises(dolfile.DolError):
            char_voices.install(self.image, self.ctx.state['hammerspace'], {0x00: row_of(0)})


# --------------------------------------------------------------------------
# Planner, CLI
# --------------------------------------------------------------------------

class NoArchiveEnv(slot_plan.Env):
    def sound_archive(self):
        return False


class PlanTests(unittest.TestCase):
    def test_stock_slot_by_family(self):
        plan = slot_plan.plan_char_voice(make_state(), make_config(), 0x0D, 0x0E, STATE_FILE, NAMES)   # Blue Toad -> Toad
        self.assertEqual(plan.config['character_voices'], [{'id': '0x0D', 'voice': '0x0D'}])
        plan = slot_plan.plan_char_voice(make_state(), make_config(), 0x0E, 0x09, STATE_FILE, NAMES)
        self.assertEqual((plan.action, plan.target), ('char_voice', 0x0E))
        self.assertEqual(plan.config['character_voices'], [{'id': '0x0E', 'voice': '0x09'}])
        self.assertEqual(plan.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        self.assertEqual(plan.effects['char_voice'], 'Bowser (0x09)')
        self.assertIn('sound_NA', plan.notes[-1])
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
        with self.assertRaises(slot_plan.PlanError) as err:
            slot_plan.plan_char_voice(make_state(), make_config(), 0x0D, 0x09, STATE_FILE, NAMES, NoArchiveEnv())
        self.assertIn('1_Input/sound_NA', str(err.exception))
        reset = slot_plan.plan_char_voice(make_state(), {**make_config(), 'character_voices': [{'id': '0x0D', 'voice': '0x09'}]},
                                          0x0D, None, STATE_FILE, NAMES, NoArchiveEnv())    # a reset needs no archive
        self.assertNotIn('character_voices', reset.config)

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

    def test_file_env_sound_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = slot_cli.FileEnv(None, None, tmp)
            self.assertFalse(env.sound_archive())
            os.makedirs(os.path.join(tmp, 'sound_NA'))
            open(os.path.join(tmp, 'sound_NA', 'MY2.brsar'), 'wb').close()
            self.assertTrue(env.sound_archive())

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
