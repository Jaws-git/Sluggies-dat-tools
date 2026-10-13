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


def build(config, keys=STEPS, sound=True, clip_dir=None):
    """The roster steps on the synthetic DOL with the synthetic voice archive (``sound=False``: none)."""
    image = image_for_grid()
    fill_voice_tables(image)
    ctx = steps.RosterContext(dol=image, dat=None, config=config, sound=archive_bytes() if sound else None,
                              clip_dir=clip_dir)
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

    def test_entries_with_clips(self):
        got = char_voices.parse({'character_voices': [
            {'id': '0x66', 'voice': '0x09', 'clips': {'v1': 'a.rwav', 'v13': 'b.wav'}, 'pack': 'P'},
            {'id': '0x67', 'clips': {'v3': 'c.rwav'}}]}, HEADS)
        self.assertEqual(got[0x66], char_voices.Choice(0x09, {0: 'a.rwav', 10: 'b.wav'}, 'P'))
        self.assertEqual(got[0x67], char_voices.Choice(None, {1: 'c.rwav'}, None))
        self.assertNotEqual(got[0x66], 0x09)                                    # clips: not a plain stock voice

    def test_refusals(self):
        for bad in ([{'id': '0x66'}], [{'voice': '0x09'}], [{'id': '0x66', 'voice': '0x47'}],
                    [{'id': '0x100', 'voice': '0x00'}], [{'id': '0x66', 'voice': '0x00'}, {'id': 102, 'voice': 9}],
                    {'id': '0x66', 'voice': '0x00'}, [{'id': '0x66', 'clips': {}}],
                    [{'id': '0x66', 'clips': {'v2': 'x.rwav'}}], [{'id': '0x66', 'clips': {'v1': ''}}],
                    [{'id': '0x66', 'voice': '0x00', 'pack': 3}]):
            with self.subTest(bad=bad), self.assertRaises(char_voices.CharVoiceConfigError):
                char_voices.parse({'character_voices': bad}, HEADS)

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
        self.assertEqual({c: v['voice'] for c, v in self.ctx.state['char_voices'].items()},
                         {0x66: 0x0D, 0x00: 0x09, 0x67: 0x09, 0x68: 0x0D, 0x47: 0x00})
        self.assertTrue(all(v['clips'] == {} and v['pack'] is None for v in self.ctx.state['char_voices'].values()))

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


def write_wav(path, seconds=0.05, rate=44100, channels=2):
    """A short stereo 16-bit test tone."""
    import numpy as np
    from SluggiesTools.Sound import wavfile
    t = np.arange(int(seconds * rate)) / rate
    tone = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    wavfile.write_wav(path, rate, [tone] * channels)
    return path


class CustomClipTests(unittest.TestCase):
    """Custom clips: ``species_clips`` (the voices step) and ``character_voices[].clips`` (the char_voices step),
    from ``.rwav`` and WAV files in a clip folder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rwav_of = synth.build_voice_archive()[1]
        cls.a = cls.rwav_of[(20, 0)]                                        # species 20's v1, as a custom clip
        cls.b = cls.rwav_of[(21, 10)]                                       # species 21's name call
        for name, data in (('a.rwav', cls.a), ('b.rwav', cls.b)):
            with open(os.path.join(cls.tmp.name, name), 'wb') as f:
                f.write(data)
        write_wav(os.path.join(cls.tmp.name, 'c.wav'))
        cls.config = {
            **{k: v for k, v in CONFIG.items() if k != 'character_voices'},
            'species_clips': [{'id': '0x0D', 'clips': {'v1': 'a.rwav', 'v13': 'b.rwav'}}],    # Toad's voice
            'character_voices': [
                {'id': '0x00', 'voice': '0x09', 'clips': {'v1': 'a.rwav', 'v13': 'b.rwav'}, 'pack': 'P'},
                {'id': '0x67', 'clips': {'v3': 'c.wav'}},                   # no stock voice: its species row + a clip
                {'id': '0x68', 'voice': '0x0D', 'clips': {'v1': 'a.rwav'}}, # Toad's voice, which plays a.rwav already
            ],
        }
        cls.image, cls.ctx = build(cls.config, clip_dir=cls.tmp.name)
        cls.out = brsar.Brsar(cls.ctx.state['sound_out'])
        cls.vanilla = brsar.Brsar(archive_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rwav(self, archive, sid):
        return brsar_write.sound_rwav(archive, archive.sounds()[sid])

    def test_species_clips_replace_the_row_words(self):
        row = struct.unpack('>12I', voices.clip_row(self.image, 0x0D))
        n = len(self.vanilla.sounds())
        self.assertGreaterEqual(row[0], n)                                   # a new sound in Toad's family file
        self.assertEqual(self.out.sounds()[row[0]].file_id, 13)
        self.assertEqual(self.rwav(self.out, row[0]), self.a)
        self.assertEqual(self.out.sounds()[row[10]].file_id, 43)             # the select file
        self.assertEqual(self.rwav(self.out, row[10]), self.b)
        self.assertEqual(row[1:10], tuple(clip(0x0D, k) for k in range(1, 10)))
        self.assertEqual(self.ctx.state['species_clips'], {(0x0D, 0): row[0], (0x0D, 10): row[10]})
        self.assertTrue(self.out.sounds()[row[0]].name.startswith('zzs'))
        # Mario's square speaks with Toad's row (stock_voices), replaced clips included
        self.assertEqual(voices.clip_row(self.image, 0x00), voices.clip_row(self.image, 0x0D))

    def test_character_clips(self):
        n = len(self.vanilla.sounds())
        table = table_of(self.image, 0x00)                                   # Bowser's voice + two custom clips
        self.assertEqual(self.rwav(self.out, table[0]), self.a)
        self.assertEqual(self.out.sounds()[table[0]].file_id, 13)            # in its family file (Toad's, the swap)
        self.assertEqual(self.rwav(self.out, table[10]), self.b)
        self.assertEqual(self.out.sounds()[table[10]].file_id, 43)
        for k in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11):                            # the rest: Bowser's copies
            self.assertEqual(self.rwav(self.out, table[k]), self.rwav(self.vanilla, clip(9, k)), k)
        # 0x00 and the species clip play the same a.rwav in the same file and slot: one sound serves both
        self.assertEqual(table[0], struct.unpack('>12I', voices.clip_row(self.image, 0x0D))[0])
        table = table_of(self.image, 0x67)                                   # species row + one WAV clip
        self.assertEqual([table[k] for k in range(12) if k != 1], [clip(0x0D, k) if k not in (0, 10) else
                                                                  struct.unpack('>12I', voices.clip_row(self.image, 0))[k]
                                                                  for k in range(12) if k != 1])
        self.assertGreaterEqual(table[1], n)
        wave = brsar.parse_rwav(self.rwav(self.out, table[1]))
        self.assertEqual((wave.format, wave.rate, len(wave.channels)), (brsar.ADPCM, 32000, 1))
        self.assertEqual(wave.samples, 1600)                                 # 0.05 s at 32 kHz
        self.assertEqual(table_of(self.image, 0x68), list(struct.unpack('>12I', voices.clip_row(self.image, 0x0D))))

    def test_state_and_manifest(self):
        entry = self.ctx.state['char_voices'][0x00]
        self.assertEqual((entry['voice'], entry['pack'], sorted(entry['clips'])), (0x09, 'P', [0, 10]))
        toad_row = struct.unpack('>12I', voices.clip_row(self.image, 0x0D))
        self.assertEqual(self.ctx.state['char_voices'][0x68]['clips'], {0: toad_row[0]})   # plays that sound already
        self.assertEqual(entry['clips'], {0: toad_row[0], 10: toad_row[10]})                # the species clips' sounds
        mf = state._manifest(self.image)
        self.assertEqual(manifest.character_voices(mf)[0x00]['clips'], entry['clips'])
        self.assertEqual(manifest.character_voices(mf)[0x67]['voice'], None)
        self.assertEqual(mf['character_voices'][-1][:2], [0x68, 0x0D])                 # the plain shape
        self.assertEqual(manifest.species_clips(mf), self.ctx.state['species_clips'])
        result = state.read_state(self.image)
        chars = {c['id']: c for c in result['characters']}
        self.assertEqual((chars[0x00]['char_voice'], chars[0x00]['char_pack'], sorted(chars[0x00]['char_clip_ids'])),
                         (0x09, 'P', ['v1', 'v13']))
        self.assertEqual((chars[0x67]['char_voice'], chars[0x67]['voice']), (None, 0x0D))   # its species' (swapped)
        toad = next(sq for sq in result['squares'] if sq['kind'] == 'stock' and sq['head'] == 0x0D)
        self.assertEqual(sorted(toad['clip_ids']), ['v1', 'v13'])
        state.clip_hashes(result, self.out)
        import hashlib
        self.assertEqual(chars[0x00]['char_clips']['v1'], hashlib.sha1(self.a).hexdigest())
        self.assertEqual(toad['clips']['v13'], hashlib.sha1(self.b).hexdigest())
        self.assertIsNone(chars[0x01]['char_clips'])
        self.assertEqual(pack.fingerprint(result, chars[0x00], lambda ref: None)['clips'], chars[0x00]['char_clips'])
        self.assertEqual(pack.fingerprint(result, chars[0x0D], lambda ref: None)['species_clips'], toad['clips'])

    def test_deterministic(self):
        image, ctx = build(self.config, clip_dir=self.tmp.name)
        self.assertEqual(image.to_bytes(), self.image.to_bytes())
        self.assertEqual(ctx.state['sound_out'], self.ctx.state['sound_out'])

    def test_refusals(self):
        base = {k: v for k, v in CONFIG.items() if k != 'character_voices'}
        with self.assertRaises(char_voices.CharVoiceConfigError) as err:
            build({**base, 'character_voices': [{'id': '0x00', 'clips': {'v1': 'missing.rwav'}}]}, clip_dir=self.tmp.name)
        self.assertIn('not found', str(err.exception))
        with self.assertRaises(char_voices.CharVoiceConfigError) as err:
            build({**base, 'character_voices': [{'id': '0x00', 'clips': {'v1': 'a.rwav'}}]}, sound=False,
                  clip_dir=self.tmp.name)
        self.assertIn('1_Input/sound_NA', str(err.exception))
        from SluggiesTools.Roster import clip_files
        with self.assertRaises(clip_files.ClipConfigError):
            build({**base, 'species_clips': [{'id': '0x66', 'clips': {'v1': 'a.rwav'}}]}, clip_dir=self.tmp.name)
        with self.assertRaises(clip_files.ClipConfigError) as err:
            build({**base, 'species_clips': [{'id': '0x00', 'clips': {'v1': 'a.rwav'}}]}, sound=False,
                  clip_dir=self.tmp.name)
        self.assertIn('sound archive', str(err.exception))


# --------------------------------------------------------------------------
# Planner, CLI
# --------------------------------------------------------------------------

class NoArchiveEnv(slot_plan.Env):
    def sound_archive(self):
        return False


class PackEnv(slot_plan.Env):
    """Voice packs and clip files without disk: ``packs`` {name: CustomPack}, ``clips`` {path: RWAV bytes}."""

    def __init__(self, packs=None, clips=None):
        self.packs, self.clips = packs or {}, clips or {}

    def sound_pack(self, name):
        return self.packs.get(name)

    def read_clip(self, path):
        if path not in self.clips:
            raise slot_plan.PlanError(f'{path}: not a clip')
        return self.clips[path]


def make_pack(tmp, name, voice, clips):
    """A custom pack folder with ``clips`` {moment: RWAV bytes}."""
    from SluggiesTools.Sound import sound_packs
    p = sound_packs.create_pack(tmp, name, voice)
    for moment, data in clips.items():
        sound_packs.set_clip(p, moment, data)
    return p


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
        e = slot_plan.parse_edits([{'op': 'char_voice', 'id': '0x0D', 'pack': 'P'},
                                   {'op': 'species_clip', 'id': '0x0D', 'moment': 'v1', 'file': 'x.wav', 'origin': 'o'},
                                   {'op': 'species_clip', 'id': '0x0D', 'moment': 'v13', 'file': None}])
        self.assertEqual([x.to_json() for x in e],
                         [{'op': 'char_voice', 'id': '0x0D', 'pack': 'P', 'source': None},
                          {'op': 'species_clip', 'id': '0x0D', 'moment': 'v1', 'file': 'x.wav', 'origin': 'o'},
                          {'op': 'species_clip', 'id': '0x0D', 'moment': 'v13', 'file': None}])
        for bad in ({'op': 'species_clip', 'id': '0x0D', 'file': 'x'}, {'op': 'species_clip', 'id': '0x0D', 'moment': 'v2'},
                    {'op': 'char_voice', 'id': '0x0D', 'pack': ''}):
            with self.subTest(bad=bad), self.assertRaises(slot_plan.PlanError):
                slot_plan.parse_edits([bad])


class PackPlanTests(unittest.TestCase):
    """``plan_char_voice`` with a voice pack, ``plan_species_clip``, their merge rules and the paste."""

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        rwav_of = synth.build_voice_archive()[1]
        self.a, self.b = rwav_of[(20, 0)], rwav_of[(21, 10)]
        from SluggiesTools.Roster import clip_files
        self.name_a, self.name_b = clip_files.file_name(self.a), clip_files.file_name(self.b)
        self.pack = make_pack(self.tmp, 'Rosa', 0x09, {'v1': self.a, 'v13': self.b})
        self.empty = make_pack(self.tmp, 'Empty', None, {})
        self.env = PackEnv({'Rosa': self.pack, 'Empty': self.empty}, {'/c.wav': self.a})

    def test_pack_on_a_slot(self):
        plan = slot_plan.plan_char_voice(make_state(), make_config(), 0x0E, None, STATE_FILE, NAMES, self.env, pack='Rosa')
        self.assertEqual(plan.config['character_voices'],
                         [{'id': '0x0E', 'voice': '0x09', 'clips': {'v1': self.name_a, 'v13': self.name_b}, 'pack': 'Rosa'}])
        self.assertEqual(plan.clips, {self.name_a: self.a, self.name_b: self.b})
        self.assertEqual(plan.effects['char_voice'], 'pack Rosa (2 custom, rest Bowser (0x09)\'s)')
        self.assertEqual(plan.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        other = slot_plan.plan_char_voice(make_state(), make_config(), 0x0E, 0x00, STATE_FILE, NAMES, self.env, pack='Rosa')
        self.assertEqual(other.config['character_voices'][0]['voice'], '0x00')   # the source wins over the base
        again = slot_plan.plan_char_voice(make_state(), plan.config, 0x0E, None, STATE_FILE, NAMES, self.env, pack='Rosa')
        self.assertTrue(again.nothing)
        back = slot_plan.plan_char_voice(make_state(), plan.config, 0x0E, 0x09, STATE_FILE, NAMES, self.env)
        self.assertEqual(back.config['character_voices'], [{'id': '0x0E', 'voice': '0x09'}])   # the clips go
        self.assertEqual(back.clips, {})
        gone = slot_plan.plan_char_voice(make_state(), plan.config, 0x0E, None, STATE_FILE, NAMES, self.env)
        self.assertNotIn('character_voices', gone.config)

    def test_pack_refusals(self):
        for pack, env in (('Nope', self.env), ('Empty', self.env), ('Rosa', NoArchiveEnv())):
            with self.subTest(pack=pack), self.assertRaises(slot_plan.PlanError):
                slot_plan.plan_char_voice(make_state(), make_config(), 0x0E, None, STATE_FILE, NAMES, env, pack=pack)
        self.empty.voice = 0x09                        # a pack with a base voice and no clips: the base alone
        plan = slot_plan.plan_char_voice(make_state(), make_config(), 0x0E, None, STATE_FILE, NAMES, self.env, pack='Empty')
        self.assertEqual(plan.config['character_voices'], [{'id': '0x0E', 'voice': '0x09'}])

    def test_species_clip(self):
        plan = slot_plan.plan_species_clip(make_state(), make_config(), 0x0E, 'v1', '/c.wav', self.env, STATE_FILE, NAMES)
        self.assertEqual(plan.config['species_clips'], [{'id': '0x0D', 'clips': {'v1': self.name_a}}])  # Blue Toad -> Toad
        self.assertEqual(plan.clips, {self.name_a: self.a})
        self.assertEqual(plan.effects['species_clips'], 'v1 from c.wav')
        self.assertEqual((plan.action, plan.target), ('species_clip', 0x0E))
        again = slot_plan.plan_species_clip(make_state(), plan.config, 0x0D, 'v1', '/c.wav', self.env, STATE_FILE, NAMES)
        self.assertTrue(again.nothing)
        reset = slot_plan.plan_species_clip(make_state(), plan.config, 0x0D, 'v1', None, self.env, STATE_FILE, NAMES)
        self.assertNotIn('species_clips', reset.config)
        self.assertEqual(reset.effects['species_clips'], 'v1 back to the stock clip')
        self.assertTrue(slot_plan.plan_species_clip(make_state(), make_config(), 0x0D, 'v1', None, self.env,
                                                    STATE_FILE, NAMES).nothing)
        for moment, file, env in (('v2', '/c.wav', self.env), ('v1', '/missing.wav', self.env),
                                  ('v1', '/c.wav', NoArchiveEnv())):
            with self.subTest(moment=moment, file=file), self.assertRaises(slot_plan.PlanError):
                slot_plan.plan_species_clip(make_state(), make_config(), 0x0D, moment, file, env, STATE_FILE, NAMES)

    def test_batch_rules(self):
        edits = slot_plan.parse_edits([
            {'op': 'species_clip', 'id': '0x0D', 'moment': 'v1', 'file': '/c.wav'},
            {'op': 'species_clip', 'id': '0x0E', 'moment': 'v1', 'file': '/c.wav'},      # the same species: replaces
            {'op': 'species_clip', 'id': '0x0D', 'moment': 'v13', 'file': '/c.wav'},
            {'op': 'species_clip', 'id': '0x0D', 'moment': 'v13', 'file': None},          # the same moment: replaces
            {'op': 'char_voice', 'id': '0x66', 'pack': 'Rosa'},
            {'op': 'char_voice', 'id': '0x67', 'source': '0x09'}])
        b = slot_plan.plan_batch(make_state(), make_config(), edits, self.env, STATE_FILE, NAMES)
        self.assertTrue(b.ok, b.refused)
        # the second v1 edit changes nothing (skipped); the v13 reset replaces the earlier v13 edit and, against the
        # game, changes nothing either
        self.assertEqual([(e.op, e.cid, e.moment) for e, _p in b.plans],
                         [('species_clip', 0x0D, 'v1'), ('char_voice', 0x66, None), ('char_voice', 0x67, None)])
        self.assertEqual([(e.op, e.cid, e.moment) for e, _p in b.skipped],
                         [('species_clip', 0x0E, 'v1'), ('species_clip', 0x0D, 'v13')])
        self.assertEqual(b.config['species_clips'], [{'id': '0x0D', 'clips': {'v1': self.name_a}}])
        self.assertEqual(b.config['character_voices'][0]['pack'], 'Rosa')
        self.assertEqual(set(b.clips), {self.name_a, self.name_b})
        self.assertTrue(any('v13 clip edit is replaced' in n for n in b.notes))
        merged, _notes, _ = slot_plan.merge_edits(slot_plan.parse_edits([
            {'op': 'char_voice', 'id': '0x66', 'pack': 'Rosa'}, {'op': 'clear', 'id': '0x66'},
            {'op': 'species_clip', 'id': '0x66', 'moment': 'v1', 'file': '/c.wav'}]))
        self.assertEqual([e.op for e in merged], ['clear', 'species_clip'])          # a clear leaves species clips

    def test_paste_copies_the_whole_entry(self):
        from SluggiesTools.tests import test_slot_copy as tc
        world = tc.make_world()
        config = tc.make_config()
        config['character_voices'] = [{'id': '0x0E', 'voice': '0x09', 'clips': {'v1': self.name_a}, 'pack': 'Rosa'}]
        st = world.state()
        next(c for c in st['characters'] if c['id'] == 0x0E).update(char_voice=0x09, char_clip_ids={'v1': 1},
                                                                    char_pack='Rosa')
        plan = slot_plan.plan_copy(st, config, config, 0x0D, 0x0E, tc.CopyEnv(world), tc.STATE_FILE, tc.NAMES)
        entries = {e['id']: e for e in plan.config['character_voices']}
        self.assertEqual(entries['0x0D'], {'id': '0x0D', 'voice': '0x09', 'clips': {'v1': self.name_a}, 'pack': 'Rosa'})
        self.assertIn('pack Rosa', plan.effects['char_voice'])


class CliTests(unittest.TestCase):
    def test_batch_of_one(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(slot_cli, 'run', return_value=slot_plan.Batch(None)) as run:
            self.assertEqual(slot_cli.main(['--char-voice', '0x66', '0x09', '--output-dir', os.path.join(tmp, 'out')]), 0)
            self.assertEqual(slot_cli.main(['--char-voice', '0x66', '-', '--output-dir', os.path.join(tmp, 'out')]), 0)
        self.assertEqual([(e.op, e.cid, e.source) for e in run.call_args_list[0].args[0]], [('char_voice', 0x66, 0x09)])
        self.assertEqual([(e.op, e.source) for e in run.call_args_list[1].args[0]], [('char_voice', None)])

    def test_parse_voice_source(self):
        self.assertEqual(slot_cli.parse_voice_source('0x09', '0x66'), (0x66, 0x09, None))
        self.assertEqual(slot_cli.parse_voice_source('pack:Rosa', '0x66'), (0x66, None, 'Rosa'))
        self.assertEqual(slot_cli.parse_voice_source('0x09+pack:Rosa Lina', '0x66'), (0x66, 0x09, 'Rosa Lina'))
        self.assertEqual(slot_cli.parse_voice_source('-', '0x66'), (0x66, None, None))
        with self.assertRaises(slot_plan.PlanError):
            slot_cli.parse_voice_source('pack:', '0x66')

    def test_species_clip_cli(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(slot_cli, 'run', return_value=slot_plan.Batch(None)) as run:
            self.assertEqual(slot_cli.main(['--species-clip', '0x00', 'v1', 'x.wav', '--output-dir', os.path.join(tmp, 'o')]), 0)
            self.assertEqual(slot_cli.main(['--species-clip', '0x00', 'v13', '-', '--output-dir', os.path.join(tmp, 'o')]), 0)
            self.assertEqual(slot_cli.main(['--char-voice', '0x00', 'pack:P', '--output-dir', os.path.join(tmp, 'o')]), 0)
        first = run.call_args_list[0].args[0][0]
        self.assertEqual((first.op, first.cid, first.moment, os.path.basename(first.file)), ('species_clip', 0, 'v1', 'x.wav'))
        self.assertEqual(run.call_args_list[1].args[0][0].file, None)
        third = run.call_args_list[2].args[0][0]
        self.assertEqual((third.op, third.source, third.pack), ('char_voice', None, 'P'))

    def test_file_env_packs_and_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, '1_Input', 'sound_NA'))
            with open(os.path.join(tmp, '1_Input', 'sound_NA', 'MY2.brsar'), 'wb') as f:
                f.write(archive_bytes())
            env = slot_cli.FileEnv(None, None, os.path.join(tmp, '1_Input'))
            self.assertIsNone(env.sound_pack('Nope'))
            rwav = synth.build_voice_archive()[1][(20, 0)]
            make_pack(tmp, 'Rosa', 0x09, {'v1': rwav})
            self.assertEqual(env.sound_pack('Rosa').rwav_bytes('v1'), rwav)
            path = os.path.join(tmp, 'a.rwav')
            with open(path, 'wb') as f:
                f.write(rwav)
            self.assertEqual(env.read_clip(path), rwav)
            self.assertEqual(env.read_clip(write_wav(os.path.join(tmp, 'c.wav')))[:4], b'RWAV')   # encoded on the fly
            with self.assertRaises(slot_plan.PlanError):
                env.read_clip(os.path.join(tmp, 'missing.wav'))

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
