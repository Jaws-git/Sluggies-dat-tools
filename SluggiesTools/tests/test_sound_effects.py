"""Sound effect replacement: ``brsar_write.replace_waves`` / ``rwav_from_channels`` and ``Sound/effects`` (encoding,
the ``Sound_FX`` library, applying it) on the synthetic archive of ``test_sound_export``."""

import os
import pathlib
import random
import shutil
import struct
import sys
import tempfile
import unittest

import numpy as np

TESTS_DIR = pathlib.Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _p in (TOOLS_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from Sound import brsar, brsar_write, dsp_adpcm, effects, wavfile    # noqa: E402
from Sound.brsar_write import NewSound                               # noqa: E402
import synthetic_sound as synth                                      # noqa: E402
from test_brsar_write import smooth                                  # noqa: E402
from test_sound_export import make_archive                           # noqa: E402


def mono_template(rng, samples=30, looped=False):
    return synth.build_rwav([(synth.random_adpcm(rng, samples), samples, synth.random_coefs(rng), 0, 0)], 32000,
                            looped=looped)


def encode_lanes(channels, coefs, loop_start=None):
    lanes = []
    for x in channels:
        encoded, _ = dsp_adpcm.encode(x, coefs)
        decoded = dsp_adpcm.decode([dsp_adpcm.Lane(encoded, len(x), coefs)])[0]
        context = (0, 0, 0)
        if loop_start is not None:
            context = (encoded[loop_start // 14 * 8], int(decoded[loop_start - 1]), int(decoded[loop_start - 2]))
        lanes.append((coefs, encoded, context))
    return lanes


GOOD = [0, 0, 2048, 0, 0, 2048, 1024, 1024, 3072, -1024, 4096, -2048, 1536, 512, 2560, -512]


class RwavFromChannelsTests(unittest.TestCase):
    def test_mono_looped(self):
        rng = random.Random(3)
        x = smooth(100, np.random.default_rng(3), 3000)
        rv = brsar_write.rwav_from_channels(mono_template(rng), 22050, 100, encode_lanes([x], GOOD, 28), 28)
        wave = brsar.parse_rwav(rv)
        self.assertEqual((wave.format, wave.rate, wave.samples, wave.loop, len(wave.channels)),
                         (brsar.ADPCM, 22050, 100, (28, 100), 1))
        self.assertEqual(wave.channels[0].coefs, GOOD)
        encoded, _ = dsp_adpcm.encode(x, GOOD)
        self.assertEqual(brsar.decode_waves([wave])[0][0].tolist(),
                         dsp_adpcm.decode([dsp_adpcm.Lane(encoded, 100, GOOD)])[0].tolist())
        self.assertEqual(len(rv) % 0x20, 0)
        self.assertEqual(struct.unpack_from('>I', rv, 8)[0], len(rv))

    def test_stereo(self):
        rng = random.Random(4)
        template = synth.build_rwav([(synth.random_adpcm(rng, 20), 20, synth.random_coefs(rng), 0, 0)] * 2, 32000)
        left, right = smooth(60, np.random.default_rng(1), 4000), smooth(60, np.random.default_rng(2), 2000)
        rv = brsar_write.rwav_from_channels(template, 32000, 60, encode_lanes([left, right], GOOD))
        wave = brsar.parse_rwav(rv)
        self.assertEqual((wave.samples, wave.loop, len(wave.channels)), (60, None, 2))
        decoded = brsar.decode_waves([wave])[0]
        for x, got in zip((left, right), decoded):
            encoded, _ = dsp_adpcm.encode(x, GOOD)
            self.assertEqual(got.tolist(), dsp_adpcm.decode([dsp_adpcm.Lane(encoded, 60, GOOD)])[0].tolist())

    def test_refusals(self):
        rng = random.Random(5)
        x = smooth(30, np.random.default_rng(5))
        with self.assertRaises(brsar_write.WriteError):           # two channels for a mono wave
            brsar_write.rwav_from_channels(mono_template(rng), 32000, 30, encode_lanes([x, x], GOOD))
        with self.assertRaises(brsar_write.WriteError):           # a PCM template
            brsar_write.rwav_from_channels(synth.build_rwav([[1, 2, 3]], 32000, fmt=1), 32000, 30,
                                           encode_lanes([x], GOOD))


class ReplaceWavesTests(unittest.TestCase):
    def setUp(self):
        self.data, _expected = make_archive()
        self.archive = brsar.Brsar(self.data)
        self.sounds = {s.name: s for s in self.archive.sounds()}
        rng = random.Random(8)
        self.new = brsar_write.rwav_from_channels(mono_template(rng), 32000, 40,
                                                  encode_lanes([smooth(40, np.random.default_rng(8))], GOOD))

    def test_nothing_is_identity(self):
        self.assertEqual(brsar_write.replace_waves(self.data, {}), self.data)

    def test_replaced_in_place(self):
        hit = self.sounds['hit_a']
        out = brsar_write.replace_waves(self.data, {hit.index: self.new})
        b = brsar.Brsar(out)
        self.assertEqual(brsar_write.sound_rwav(b, hit.index), self.new)
        old_waves = brsar_write.rwar_split(self.archive.file_bytes(0)[1])
        new_waves = brsar_write.rwar_split(b.file_bytes(0)[1])
        self.assertEqual(new_waves, [self.new] + old_waves[1:])                     # wave 0 swapped, no growth
        self.assertEqual(b.file_bytes(0)[0], self.archive.file_bytes(0)[0])          # the RWSD as it was
        self.assertEqual(b.file_bytes(2), self.archive.file_bytes(2))
        self.assertEqual(brsar_write.sound_rwav(b, self.sounds['chord_b'].index),
                         brsar_write.sound_rwav(self.archive, self.sounds['chord_b'].index))
        files = b.files()
        self.assertEqual((files[0].size, files[0].wave_size), tuple(len(p) for p in b.file_bytes(0)))
        self.assertEqual([(s.name, s.type, s.file_id, s.wave_entry) for s in b.sounds()],
                         [(s.name, s.type, s.file_id, s.wave_entry) for s in self.archive.sounds()])
        self.assertEqual(struct.unpack_from('>I', out, 8)[0], len(out))
        # replaced again, and back
        again = brsar_write.replace_waves(out, {hit.index: brsar_write.sound_rwav(self.archive, hit.index)})
        self.assertEqual(brsar.Brsar(again).file_bytes(0), self.archive.file_bytes(0))

    def _shared(self):
        """The archive plus ``zz_twin``, a sound whose RWSD note plays hit_a's wave 0 too."""
        rv = brsar_write.sound_rwav(self.archive, self.sounds['hit_a'].index)
        out, (twin,) = brsar_write.add_sounds(self.data, [NewSound('zz_twin', 0, rv, self.sounds['hit_a'].index)])
        b = brsar.Brsar(out)
        group = b.groups()[0]
        item = next(i for i in group.items if i.file_id == 0)
        start = group.offset + item.offset
        r = b.b[start:start + item.size]
        at = start + brsar_write._note_offset(r, b.sounds()[twin].wave_entry)
        out = bytearray(out)
        struct.pack_into('>I', out, at, 0)
        return bytes(out), twin

    def test_shared_wave_is_left_to_the_others(self):
        data, twin = self._shared()
        hit = self.sounds['hit_a'].index
        a = brsar.Brsar(data)
        self.assertEqual(brsar_write.sound_rwav(a, twin), brsar_write.sound_rwav(a, hit))
        count = brsar.rwar_count(a.file_bytes(0)[1])
        out = brsar_write.replace_waves(data, {twin: self.new})
        b = brsar.Brsar(out)
        self.assertEqual(brsar_write.sound_rwav(b, twin), self.new)
        self.assertEqual(brsar_write.sound_rwav(b, hit), brsar_write.sound_rwav(a, hit))
        self.assertEqual(brsar.rwar_count(b.file_bytes(0)[1]), count + 1)            # appended
        # now the twin owns its wave: a second replace swaps it in place
        out2 = brsar_write.replace_waves(out, {twin: brsar_write.sound_rwav(a, hit)})
        self.assertEqual(brsar.rwar_count(brsar.Brsar(out2).file_bytes(0)[1]), count + 1)
        # both sharers at once: one appended, the other in place
        out3 = brsar_write.replace_waves(data, {twin: self.new, hit: self.new})
        b3 = brsar.Brsar(out3)
        self.assertEqual((brsar_write.sound_rwav(b3, twin), brsar_write.sound_rwav(b3, hit)), (self.new, self.new))
        self.assertEqual(brsar.rwar_count(b3.file_bytes(0)[1]), count + 1)

    def test_refusals(self):
        for sid, rv in ((0, self.new), (self.sounds['seq_test'].index, self.new), (99, self.new),
                        (self.sounds['hit_a'].index, b'RWAR' + bytes(28))):
            with self.subTest(sid=sid), self.assertRaises(brsar_write.WriteError):
                brsar_write.replace_waves(self.data, {sid: rv})


class EffectsTests(unittest.TestCase):
    def setUp(self):
        self.data, _expected = make_archive()
        self.archive = brsar.Brsar(self.data)
        self.root = tempfile.mkdtemp(prefix='sluggies_fx_test_')
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, '1_Input', 'sound_NA'))
        with open(os.path.join(self.root, '1_Input', effects.ARCHIVE), 'wb') as handle:
            handle.write(self.data)
        self.hit = next(s for s in self.archive.sounds() if s.name == 'hit_a')
        self.stock = brsar_write.sound_rwav(self.archive, self.hit)

    def wav(self, rate, channels, loop=None):
        path = os.path.join(self.root, f'in_{rate}_{len(channels)}.wav')
        wavfile.write_wav(path, rate, channels, loop)
        return path

    def test_list(self):
        found = effects.list_effects(self.archive)
        self.assertEqual([(e.name, e.group, e.channels, e.rate, e.samples, e.loop, e.shared) for e in found],
                         [('hit_a', 'group_test', 1, 32000, 50, False, 0),
                          ('chord_b', 'group_test', 2, 44100, 12, False, 0)])

    def test_encode_to_the_stock_shape(self):
        rng = np.random.default_rng(6)
        left, right = smooth(441, rng, 5000), smooth(441, rng, 5000)
        rv, notes = effects.encode(self.wav(44100, [left, right]), self.stock)
        wave = brsar.parse_rwav(rv)
        self.assertEqual((wave.format, wave.rate, len(wave.channels), wave.loop), (brsar.ADPCM, 32000, 1, None))
        self.assertAlmostEqual(wave.samples, 320, delta=2)
        self.assertTrue(any('resampled' in n for n in notes) and any('mixed down' in n for n in notes))
        # a looping stock wave: the replacement loops from the start; a WAV loop wins
        looped = brsar_write.rwar_split(self.archive.file_bytes(0)[1])[1]
        x = smooth(300, rng, 5000)
        rv, _ = effects.encode(self.wav(22050, [x]), looped)
        self.assertEqual(brsar.parse_rwav(rv).loop, (0, 300))
        rv, notes = effects.encode(self.wav(22050, [x], loop=(100, 250)), looped)
        self.assertEqual(brsar.parse_rwav(rv).loop, (98, 250))                       # snapped to the frame
        self.assertTrue(any('WAV\'s loop' in n for n in notes))
        with self.assertRaises(effects.EffectError):                                 # a PCM stock wave
            effects.encode(self.wav(22050, [x]), brsar_write.sound_rwav(self.archive, 3))

    def test_library_apply_and_sync(self):
        rng = np.random.default_rng(7)
        rv, _ = effects.encode(self.wav(32000, [smooth(200, rng, 5000)]), self.stock)
        effects.set_effect(self.root, 'hit_a', rv, origin='C:/x/boom.wav')
        library = effects.load_library(self.root)
        self.assertEqual((library['hit_a'].rwav, library['hit_a'].origin, library['hit_a'].seconds),
                         ('hit_a.rwav', 'C:/x/boom.wav', round(200 / 32000, 3)))
        out, lines = effects.apply_library(self.data, self.root)
        self.assertEqual(brsar_write.sound_rwav(brsar.Brsar(out), self.hit.index), rv)
        self.assertIn('hit_a', lines[-1])
        # the output archive, made from 1_Input's
        self.assertEqual(effects.sync_output(self.root), (1, []))
        output = os.path.join(self.root, '3_Output_Dat', effects.ARCHIVE)
        self.assertEqual(brsar_write.sound_rwav(brsar.Brsar.from_file(output), self.hit.index), rv)
        self.assertEqual(effects.sync_output(self.root), (0, []))                     # nothing left to do
        self.assertTrue(effects.remove_effect(self.root, 'hit_a'))
        self.assertEqual(effects.sync_output(self.root), (1, []))
        with open(output, 'rb') as handle:
            self.assertEqual(brsar.Brsar(handle.read()).file_bytes(0), self.archive.file_bytes(0))
        self.assertEqual(effects.apply_library(self.data, self.root), (self.data, []))

    def test_library_problems(self):
        effects.set_effect(self.root, 'no_such_sound', self.stock)
        stereo = brsar_write.rwav_from_channels(
            synth.build_rwav([(bytes(8), 14, GOOD, 0, 0)] * 2, 32000), 32000, 14, encode_lanes([np.zeros(14, np.int16)] * 2, GOOD))
        effects.set_effect(self.root, 'hit_a', stereo)
        waves, problems = effects.library_waves(self.archive, self.root)
        self.assertEqual(waves, {})
        self.assertEqual(len(problems), 2)
        out, lines = effects.apply_library(self.data, self.root)
        self.assertEqual(out, self.data)
        self.assertEqual(len(lines), 2)


if __name__ == '__main__':
    unittest.main()
