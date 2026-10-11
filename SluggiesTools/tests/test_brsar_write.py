"""The BRSAR writer (``Sound/brsar_write.py``) and the DSP-ADPCM encoder on the synthetic archive of
``test_sound_export`` / ``synthetic_sound``."""

import pathlib
import random
import struct
import sys
import unittest

import numpy as np

TESTS_DIR = pathlib.Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _p in (TOOLS_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from Sound import brsar, brsar_write, dsp_adpcm                 # noqa: E402
from Sound.brsar_write import NewSound                          # noqa: E402
from Sound.dsp_adpcm import Lane                                # noqa: E402
import synthetic_sound as synth                                 # noqa: E402
from test_sound_export import make_archive                      # noqa: E402


def smooth(n, rng, amplitude=12000):
    """A test signal with some structure (two sines plus noise), int16."""
    t = np.arange(n)
    x = amplitude * np.sin(t / 7.0) + amplitude / 3 * np.sin(t / 2.3) + rng.normal(0, 200, n)
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


class EncoderTests(unittest.TestCase):
    def test_nibble_count(self):
        for samples, nibbles in ((0, 0), (1, 3), (14, 16), (15, 19), (28, 32), (27512, 0x7AD4)):
            self.assertEqual(dsp_adpcm.samples_to_nibbles(samples), nibbles)
            self.assertEqual(dsp_adpcm.nibbles_to_samples(nibbles), samples)

    def test_matches_the_reference(self):
        rng = random.Random(4)
        np_rng = np.random.default_rng(4)
        for n in (1, 5, 14, 15, 30, 100):
            coefs = synth.random_coefs(rng)
            x = smooth(n, np_rng, 3000)
            fast, h_fast = dsp_adpcm.encode(x, coefs, 11, -9)
            slow, h_slow = dsp_adpcm.encode_reference(x, coefs, 11, -9)
            self.assertEqual((fast, h_fast), (slow, h_slow), n)
            self.assertEqual(len(fast), 8 * ((n + 13) // 14))

    def test_round_trip_is_close(self):
        np_rng = np.random.default_rng(1)
        x = smooth(400, np_rng)
        coefs = [0, 0, 2048, 0, 0, 2048, 1024, 1024, 3072, -1024, 4096, -2048, 1536, 512, 2560, -512]
        encoded, _ = dsp_adpcm.encode(x, coefs)
        decoded = np.asarray(dsp_adpcm.decode([Lane(encoded, 400, coefs)])[0], np.int64)
        error = np.abs(decoded - x.astype(np.int64))
        self.assertLess(error.mean(), 120)                     # a few dozen on a 12,000-amplitude signal
        self.assertLess(error.max(), 2000)
        self.assertEqual(dsp_adpcm.encode(np.zeros(28, np.int16), coefs)[0], bytes(16))

    def test_refusals(self):
        with self.assertRaises(ValueError):
            dsp_adpcm.encode([1, 2, 3], [0] * 15)


class RwavRwarTests(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(9)
        self.coefs = synth.random_coefs(self.rng)
        self.template = synth.build_rwav([(synth.random_adpcm(self.rng, 30), 30, self.coefs, 3, 4)], 32000)

    def test_rwar_split_and_build(self):
        rwavs = [self.template, synth.build_rwav([(synth.random_adpcm(self.rng, 7), 7, self.coefs, 0, 0)], 22050)]
        rwar = synth.build_rwar(rwavs)
        self.assertEqual(brsar_write.rwar_split(rwar), rwavs)
        rebuilt = brsar_write.rwar_build(rwavs + [self.template])
        self.assertEqual(brsar.rwar_count(rebuilt), 3)
        self.assertEqual([brsar.rwar_wave_bytes(rebuilt, i) for i in range(3)], rwavs + [self.template])
        self.assertEqual(brsar_write.rwar_split(rebuilt), rwavs + [self.template])
        self.assertEqual(len(rebuilt) % 0x20, 0)
        self.assertEqual(struct.unpack_from('>I', rebuilt, 8)[0], len(rebuilt))
        with self.assertRaises(brsar_write.WriteError):
            brsar_write.rwar_split(b'RWSD' + bytes(32))

    def test_rwav_build_and_coefs(self):
        self.assertEqual(brsar_write.rwav_coefs(self.template), self.coefs)
        np_rng = np.random.default_rng(2)
        x = smooth(45, np_rng, 2000)
        rv = brsar_write.rwav_build(self.template, 22050, x)
        wave = brsar.parse_rwav(rv)
        self.assertEqual((wave.format, wave.rate, wave.samples, len(wave.channels), wave.loop), (brsar.ADPCM, 22050, 45, 1, None))
        lane = wave.channels[0]
        self.assertEqual((lane.coefs, lane.h1, lane.h2), (self.coefs, 0, 0))
        encoded, _ = dsp_adpcm.encode(x, self.coefs)
        self.assertEqual(lane.data, encoded[:len(lane.data)])         # the lane holds the 45 samples' bytes
        self.assertEqual(brsar.decode_waves([wave])[0][0].tolist(), dsp_adpcm.decode_reference(Lane(encoded, 45, self.coefs)))
        self.assertEqual(len(rv) % 0x20, 0)
        self.assertEqual(struct.unpack_from('>I', rv, 8)[0], len(rv))
        self.assertEqual(brsar_write.rwav_error(rv, x), int(((brsar.decode_waves([wave])[0][0].astype(np.int64) - x) ** 2).sum()))

    def test_candidates_and_encode_clip(self):
        stereo = synth.build_rwav([[1, 2, 3], [4, 5, 6]], 44100, fmt=1)
        good = [0, 0, 2048, 0, 0, 2048, 1024, 1024, 3072, -1024, 4096, -2048, 1536, 512, 2560, -512]
        fitted = synth.build_rwav([(synth.random_adpcm(self.rng, 14), 14, good, 0, 0)], 32000)
        rwavs = [stereo, self.template, fitted] + [self.template] * 6
        picked = brsar_write.candidates(rwavs)
        self.assertEqual(len(picked), brsar_write.CANDIDATES)
        self.assertEqual(picked[:2], [self.template, fitted])
        np_rng = np.random.default_rng(3)
        x = smooth(200, np_rng)
        rv = brsar_write.encode_clip([self.template, fitted], 32000, x)
        self.assertEqual(brsar_write.rwav_coefs(rv), good)                     # random coefficients lose
        with self.assertRaises(brsar_write.WriteError):
            brsar_write.encode_clip([], 32000, x)
        with self.assertRaises(brsar_write.WriteError):
            brsar_write.rwav_coefs(stereo)

    def test_rwsd_add_entries(self):
        rwsd = synth.build_rwsd([0, 2, 0])
        grown, new = brsar_write.rwsd_add_entries(rwsd, [3, 4])
        self.assertEqual(new, [3, 4])
        self.assertEqual(brsar.rwsd_entry_count(grown), 5)
        self.assertEqual([brsar.rwsd_wave_index(grown, e) for e in range(5)], [0, 2, 0, 3, 4])
        self.assertEqual(len(grown) % 0x20, 0)
        self.assertEqual(struct.unpack_from('>I', grown, 8)[0], len(grown))
        again, _ = brsar_write.rwsd_add_entries(grown, [5])
        self.assertEqual([brsar.rwsd_wave_index(again, e) for e in range(6)], [0, 2, 0, 3, 4, 5])
        with self.assertRaises(brsar_write.WriteError):
            brsar_write.rwsd_add_entries(b'RWAR' + bytes(32), [0])


def u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


class AddSoundsTests(unittest.TestCase):
    def setUp(self):
        self.data, self.expected = make_archive()
        self.archive = brsar.Brsar(self.data)
        rng = random.Random(21)
        self.coefs = synth.random_coefs(rng)
        self.extra = synth.build_rwav([(synth.random_adpcm(rng, 20), 20, self.coefs, 1, 2)], 22050)
        self.hit = next(s for s in self.archive.sounds() if s.name == 'hit_a')
        self.chord = next(s for s in self.archive.sounds() if s.name == 'chord_b')

    def test_nothing_to_add_is_identity(self):
        out, ids = brsar_write.add_sounds(self.data, [])
        self.assertEqual((out, ids), (self.data, []))

    def test_sound_rwav_is_the_wave_byte_for_byte(self):
        rwsd, rwar = self.archive.file_bytes(0)
        self.assertEqual(brsar_write.sound_rwav(self.archive, self.hit), brsar.rwar_wave_bytes(rwar, 0))
        self.assertEqual(brsar_write.sound_rwav(self.archive, self.chord.index), brsar.rwar_wave_bytes(rwar, 2))
        with self.assertRaises(brsar_write.WriteError):
            brsar_write.sound_rwav(self.archive, 0)                            # the stream sound
        self.assertEqual(len(brsar_write.file_candidates(self.archive, 0)), 2)  # the two mono ADPCM waves

    def test_added_sounds(self):
        copied = brsar_write.sound_rwav(self.archive, self.hit)
        out, ids = brsar_write.add_sounds(self.data, [
            NewSound('zz_copy', 0, copied, self.hit.index),
            NewSound('zz_new', 0, self.extra, self.chord.index, volume=120)])
        self.assertEqual(ids, [4, 5])
        b = brsar.Brsar(out)
        sounds = b.sounds()
        old = [(s.name, s.type, s.file_id, s.player_id, s.volume, s.wave_entry) for s in self.archive.sounds()]
        self.assertEqual([(s.name, s.type, s.file_id, s.player_id, s.volume, s.wave_entry) for s in sounds[:4]], old)
        self.assertEqual([(s.name, s.type, s.file_id, s.player_id, s.volume, s.priority, s.wave_entry) for s in sounds[4:]],
                         [('zz_copy', brsar.WAVE, 0, 0, 107, 64, 3), ('zz_new', brsar.WAVE, 0, 0, 120, 64, 4)])
        self.assertEqual(b.names, self.archive.names + ['zz_copy', 'zz_new'])
        # the file grew by the two waves; every other file is as it was
        rwsd, rwar = b.file_bytes(0)
        self.assertEqual(brsar.rwar_count(rwar), 5)
        self.assertEqual([brsar.rwar_wave_bytes(rwar, i) for i in range(5)],
                         brsar_write.rwar_split(self.archive.file_bytes(0)[1]) + [copied, self.extra])
        self.assertEqual([brsar.rwsd_wave_index(rwsd, e) for e in range(5)], [0, 2, 0, 3, 4])
        self.assertEqual(b.file_bytes(2), self.archive.file_bytes(2))
        self.assertIsNone(b.file_bytes(1))
        files = b.files()
        self.assertEqual((files[0].size, files[0].wave_size), (len(rwsd), len(rwar)))
        self.assertEqual([(f.external_path, f.positions) for f in files], [(f.external_path, f.positions) for f in self.archive.files()])
        self.assertEqual([(g.name, [i.file_id for i in g.items]) for g in b.groups()], [('group_test', [2, 0])])
        self.assertEqual([(p.name, p.sound_count) for p in b.players()], [(p.name, p.sound_count) for p in self.archive.players()])
        self.assertEqual([(k.name, k.file_id) for k in b.banks()], [(k.name, k.file_id) for k in self.archive.banks()])
        # the new sounds decode to what was put in
        waves = [brsar.parse_rwav(brsar_write.sound_rwav(b, i)) for i in ids]
        decoded = brsar.decode_waves(waves)
        self.assertEqual([c.tolist() for c in decoded[0]], self.expected['hit_a'])
        self.assertEqual(waves[1].rate, 22050)
        self.assertEqual(len(decoded[1][0]), 20)
        # header, sizes and alignment
        self.assertEqual(u32(out, 8), len(out))
        symb, symb_size, info, info_size, file_, file_size = struct.unpack_from('>6I', out, 0x10)
        self.assertEqual((symb, info, file_), (0x40, 0x40 + symb_size, 0x40 + symb_size + info_size))
        self.assertEqual(file_ + file_size, len(out))
        self.assertEqual(file_ % 0x20, 0)
        for g in b.groups():
            self.assertEqual((g.offset % 0x20, g.wave_offset % 0x20), (0, 0))

    def test_adding_twice_appends_again(self):
        out, ids = brsar_write.add_sounds(self.data, [NewSound('zz_a', 0, self.extra, self.hit.index)])
        out2, ids2 = brsar_write.add_sounds(out, [NewSound('zz_b', 0, self.extra, ids[0])])
        self.assertEqual((ids, ids2), ([4], [5]))
        b = brsar.Brsar(out2)
        self.assertEqual([s.name for s in b.sounds()][4:], ['zz_a', 'zz_b'])
        self.assertEqual(brsar.rwar_count(b.file_bytes(0)[1]), 5)
        self.assertEqual(brsar_write.sound_rwav(b, 5), self.extra)

    def test_refusals(self):
        for bad in (NewSound('hit_a', 0, self.extra, 1), NewSound('', 0, self.extra, 1),
                    NewSound('zz', 1, self.extra, 1), NewSound('zz', 9, self.extra, 1),
                    NewSound('zz', 0, b'RWAR' + bytes(32), 1), NewSound('zz', 0, self.extra, 0),
                    NewSound('zz', 0, self.extra, 1, volume=128)):
            with self.subTest(bad=bad.name or 'empty'), self.assertRaises(brsar_write.WriteError):
                brsar_write.add_sounds(self.data, [bad])
        with self.assertRaises(brsar_write.WriteError):
            brsar_write.add_sounds(self.data, [NewSound('zz', 0, self.extra, 1), NewSound('zz', 0, self.extra, 1)])


if __name__ == '__main__':
    unittest.main()
