"""Sound export: the DSP-ADPCM decoder, the BRSAR / BRSTM readers, the WAV writer and ``export_sounds`` on a
synthetic archive (``synthetic_sound.py``)."""

import json
import os
import pathlib
import random
import struct
import sys
import tempfile
import unittest
import wave
from unittest import mock

import numpy as np

TESTS_DIR = pathlib.Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
ROOT_DIR = TOOLS_DIR.parent
for _p in (TOOLS_DIR, ROOT_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from Sound import brsar, brstm, dsp_adpcm, wavfile          # noqa: E402
from Sound import export_sounds                              # noqa: E402
from Sound.dsp_adpcm import Lane                              # noqa: E402
import synthetic_sound as synth                               # noqa: E402
import start                                                  # noqa: E402


def read_wav(path):
    with wave.open(str(path), 'rb') as handle:
        frames = handle.getnframes()
        channels = handle.getnchannels()
        data = np.frombuffer(handle.readframes(frames), '<i2').reshape(frames, channels)
        return handle.getframerate(), [data[:, c].tolist() for c in range(channels)]


class DspAdpcmTests(unittest.TestCase):
    def test_nibble_arithmetic(self):
        self.assertEqual(dsp_adpcm.nibbles_to_samples(0x7AD4), 27512)      # Mario's v1 clip
        self.assertEqual(dsp_adpcm.nibbles_to_samples(16), 14)
        self.assertEqual(dsp_adpcm.nibbles_to_samples(17), 14)              # a header nibble adds nothing
        self.assertEqual(dsp_adpcm.nibbles_to_samples(18), 14)
        self.assertEqual(dsp_adpcm.nibbles_to_samples(19), 15)
        self.assertEqual(dsp_adpcm.nibble_to_sample(2), 0)
        self.assertEqual(dsp_adpcm.nibble_to_sample(16 + 5), 14 + 3)
        self.assertEqual(dsp_adpcm.samples_to_bytes(14), 8)
        self.assertEqual(dsp_adpcm.samples_to_bytes(15), 10)                # header byte + half a byte
        self.assertEqual(dsp_adpcm.samples_to_bytes(16), 10)
        self.assertEqual(dsp_adpcm.samples_to_bytes(17), 11)
        self.assertEqual(dsp_adpcm.samples_to_bytes(28), 16)

    def test_known_values_with_zero_coefficients(self):
        # c1 = c2 = 0: a sample is its nibble times 2**scale.
        frame = bytes([0x03, 0x1F, 0x70, 0x8A, 0, 0, 0, 0])
        lane = Lane(frame, 6, [0] * 16)
        self.assertEqual(dsp_adpcm.decode([lane])[0].tolist(), [8, -8, 56, 0, -64, -48])
        self.assertEqual(dsp_adpcm.decode_reference(lane), [8, -8, 56, 0, -64, -48])

    def test_matches_reference_on_random_lanes(self):
        rng = random.Random(7)
        lanes = []
        for i in range(40):
            samples = rng.choice([1, 13, 14, 15, 27, 28, 100, 333, 1000])
            strong = i % 5 == 0                                             # coefficients that make it clip
            lanes.append(Lane(synth.random_adpcm(rng, samples), samples, synth.random_coefs(rng, strong),
                              rng.randint(-32768, 32767) if i % 3 else 0, rng.randint(-32768, 32767) if i % 3 else 0))
        decoded = dsp_adpcm.decode(lanes)
        for lane, got in zip(lanes, decoded):
            self.assertEqual(got.dtype, np.int16)
            self.assertEqual(got.tolist(), dsp_adpcm.decode_reference(lane))

    def test_batches_split_by_size(self):
        rng = random.Random(3)
        lanes = [Lane(synth.random_adpcm(rng, n), n, synth.random_coefs(rng)) for n in (300, 20, 290, 7, 150)]
        expected = [dsp_adpcm.decode_reference(lane) for lane in lanes]
        with mock.patch.object(dsp_adpcm, 'BATCH_ELEMENTS', 400):           # at most one lane per batch
            got = dsp_adpcm.decode(lanes)
        self.assertEqual([g.tolist() for g in got], expected)

    def test_short_data_is_refused(self):
        with self.assertRaises(ValueError):
            dsp_adpcm.decode([Lane(bytes(8), 15, [0] * 16)])

    def test_empty_lane_list(self):
        self.assertEqual(dsp_adpcm.decode([]), [])


class WavFileTests(unittest.TestCase):
    def test_stereo_wav_round_trip(self):
        left = np.array([1, -2, 3, -32768, 32767], np.int16)
        right = np.array([10, 20, 30, 40, 50], np.int16)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'a', 'b.wav')
            wavfile.write_wav(path, 32000, [left, right])
            rate, channels = read_wav(path)
        self.assertEqual(rate, 32000)
        self.assertEqual(channels, [left.tolist(), right.tolist()])

    def test_loop_goes_into_a_smpl_chunk(self):
        data = wavfile.wav_bytes(22050, [np.arange(100, dtype=np.int16)], loop=(10, 60))
        at = data.find(b'smpl')
        self.assertGreater(at, 0)
        size = struct.unpack_from('<I', data, at + 4)[0]
        self.assertEqual(size, 36 + 24)
        period, note, loops = struct.unpack_from('<I', data, at + 8 + 8)[0], struct.unpack_from('<I', data, at + 8 + 12)[0], struct.unpack_from('<I', data, at + 8 + 28)[0]
        self.assertEqual((period, note, loops), (1_000_000_000 // 22050, 60, 1))
        self.assertEqual(struct.unpack_from('<6I', data, at + 8 + 36), (0, 0, 10, 59, 0, 0))
        self.assertNotIn(b'smpl', wavfile.wav_bytes(22050, [np.zeros(4, np.int16)]))

    def test_refuses_mismatched_channels(self):
        with self.assertRaises(ValueError):
            wavfile.wav_bytes(8000, [np.zeros(3, np.int16), np.zeros(4, np.int16)])


def make_archive(rng=None, missing_stream=False):
    """A mini archive: group 'group_test' with one RWSD/RWAR file holding three waves (two ADPCM mono, one of
    them looped and unreferenced; one PCM16 stereo), two wave sounds, one stream sound (external .brstm), one
    sequence sound with a bank, two players. Returns (bytes, expected: {name: [channels]}, stream expectations)."""
    rng = rng or random.Random(11)
    n1, n2, n3 = 50, 29, 12
    c1, c2 = synth.random_coefs(rng), synth.random_coefs(rng)
    a1, a2 = synth.random_adpcm(rng, n1), synth.random_adpcm(rng, n2)
    pcm_left = [rng.randint(-32768, 32767) for _ in range(n3)]
    pcm_right = [rng.randint(-32768, 32767) for _ in range(n3)]
    rwavs = [synth.build_rwav([(a1, n1, c1, 0, 0)], 32000),
             synth.build_rwav([(a2, n2, c2, 5, -7)], 22050, looped=True, loop_start_nibble=16 + 3),
             synth.build_rwav([pcm_left, pcm_right], 44100, fmt=1)]
    rwar = synth.build_rwar(rwavs)
    rwsd = synth.build_rwsd([0, 2, 0])                  # entries: wave 0, wave 2, wave 0 again; wave 1 unreferenced
    names = ['hit_a', 'bgm_test', 'seq_test', 'PLAYER_se00', 'PLAYER_BGM', 'bnk_test', 'group_test', 'chord_b']
    sounds = [
        {'name': 'bgm_test', 'file': 1, 'player': 1, 'type': brsar.STRM, 'volume': 100, 'priority': 64,
         'detail': synth.stream_detail()},
        {'name': 'hit_a', 'file': 0, 'player': 0, 'type': brsar.WAVE, 'volume': 107, 'priority': 64,
         'detail': synth.wave_detail(0)},
        {'name': 'seq_test', 'file': 2, 'player': 0, 'type': brsar.SEQ, 'volume': 64, 'priority': 64,
         'detail': synth.seq_detail(0)},
        {'name': 'chord_b', 'file': 0, 'player': 0, 'type': brsar.WAVE, 'volume': 90, 'priority': 64,
         'detail': synth.wave_detail(1)},
    ]
    banks = [{'name': 'bnk_test', 'file': 2}]
    players = [{'name': 'PLAYER_se00', 'count': 2, 'heap': 0}, {'name': 'PLAYER_BGM', 'count': 1, 'heap': 0}]
    files = [{'size': len(rwsd), 'wave_size': len(rwar), 'external': '', 'positions': [(0, 1)]},
             {'size': 4096, 'wave_size': 0, 'external': 'stream/bgm_test.brstm', 'positions': []},
             {'size': 64, 'wave_size': 0, 'external': '', 'positions': [(0, 0)]}]
    groups = [{'name': 'group_test', 'items': [{'file': 2, 'data': b'RBNK' + bytes(60), 'waves': b''},
                                               {'file': 0, 'data': rwsd, 'waves': rwar}]}]
    archive = synth.build_brsar(names, sounds, banks, players, files, groups)
    expected = {'hit_a': [dsp_adpcm.decode_reference(Lane(a1, n1, c1))],
                'chord_b': [pcm_left, pcm_right],
                'file0_wave1': [dsp_adpcm.decode_reference(Lane(a2, n2, c2, 5, -7))]}
    return archive, expected


class BrsarReaderTests(unittest.TestCase):
    def setUp(self):
        self.data, self.expected = make_archive()
        self.archive = brsar.Brsar(self.data)

    def test_tables(self):
        sounds = self.archive.sounds()
        self.assertEqual([(s.name, s.type, s.file_id, s.player_id, s.volume) for s in sounds],
                         [('bgm_test', brsar.STRM, 1, 1, 100), ('hit_a', brsar.WAVE, 0, 0, 107),
                          ('seq_test', brsar.SEQ, 2, 0, 64), ('chord_b', brsar.WAVE, 0, 0, 90)])
        self.assertEqual([s.wave_entry for s in sounds], [None, 0, None, 1])
        self.assertEqual(sounds[2].bank, 0)
        self.assertEqual([(p.name, p.sound_count) for p in self.archive.players()], [('PLAYER_se00', 2), ('PLAYER_BGM', 1)])
        self.assertEqual([(b.name, b.file_id) for b in self.archive.banks()], [('bnk_test', 2)])
        files = self.archive.files()
        self.assertEqual([f.external_path for f in files], ['', 'stream/bgm_test.brstm', ''])
        self.assertEqual([f.positions for f in files], [[(0, 1)], [], [(0, 0)]])
        groups = self.archive.groups()
        self.assertEqual([(g.name, [i.file_id for i in g.items]) for g in groups], [('group_test', [2, 0])])

    def test_file_bytes_and_waves(self):
        rwsd, rwar = self.archive.file_bytes(0)
        self.assertEqual(rwsd[:4], b'RWSD')
        self.assertEqual(rwar[:4], b'RWAR')
        self.assertIsNone(self.archive.file_bytes(1))
        self.assertEqual(brsar.rwsd_entry_count(rwsd), 3)
        self.assertEqual([brsar.rwsd_wave_index(rwsd, e) for e in range(3)], [0, 2, 0])
        self.assertEqual(brsar.rwar_count(rwar), 3)
        with self.assertRaises(brsar.BrsarError):
            brsar.rwsd_wave_index(rwsd, 3)
        w0 = brsar.parse_rwav(brsar.rwar_wave_bytes(rwar, 0))
        self.assertEqual((w0.format, w0.rate, w0.samples, len(w0.channels), w0.loop), (brsar.ADPCM, 32000, 50, 1, None))
        w1 = brsar.parse_rwav(brsar.rwar_wave_bytes(rwar, 1))
        self.assertEqual((w1.rate, w1.samples, w1.loop), (22050, 29, (15, 29)))     # nibble 19 = sample 15
        self.assertEqual((w1.channels[0].h1, w1.channels[0].h2), (5, -7))
        w2 = brsar.parse_rwav(brsar.rwar_wave_bytes(rwar, 2))
        self.assertEqual((w2.format, w2.rate, w2.samples, len(w2.channels)), (brsar.PCM16, 44100, 12, 2))
        decoded = brsar.decode_waves([w0, w1, w2])
        self.assertEqual([c.tolist() for c in decoded[0]], self.expected['hit_a'])
        self.assertEqual([c.tolist() for c in decoded[1]], self.expected['file0_wave1'])
        self.assertEqual([c.tolist() for c in decoded[2]], self.expected['chord_b'])

    def test_not_an_archive(self):
        with self.assertRaises(brsar.BrsarError):
            brsar.Brsar(b'RSTM' + bytes(64))


class BrstmReaderTests(unittest.TestCase):
    def test_adpcm_stream_decodes_blockwise_to_the_continuous_signal(self):
        data, expected = synth.build_brstm([[0] * 100, [0] * 100], 32000, block_size=16, looped=True, loop_start=28)
        stream = brstm.parse(data)
        self.assertEqual((stream.codec, stream.rate, stream.channels, stream.samples, stream.loop), (2, 32000, 2, 100, (28, 100)))
        self.assertEqual(len(stream.lanes[0]), 4)                            # 28 samples per 16-byte block
        self.assertEqual(stream.lanes[0][-1].samples, 16)
        self.assertEqual([c.tolist() for c in brstm.decode(stream)], expected)

    def test_pcm16_stream(self):
        rng = random.Random(2)
        samples = [[rng.randint(-32768, 32767) for _ in range(37)]]
        data, expected = synth.build_brstm(samples, 44100, block_size=16, codec=1)
        stream = brstm.parse(data)
        self.assertEqual((stream.codec, stream.channels, stream.samples, stream.loop), (1, 1, 37, None))
        self.assertEqual([c.tolist() for c in brstm.decode(stream)], expected)

    def test_not_a_stream(self):
        with self.assertRaises(brstm.BrstmError):
            brstm.parse(b'RSAR' + bytes(64))


class ExportSoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(export_sounds._slogger, 'info'))      # quiet
        self.enterContext(mock.patch.object(export_sounds._slogger, 'error'))
        self.sound_dir = os.path.join(self.tmp, 'sound_NA')
        self.out = os.path.join(self.tmp, 'out', '_SOUNDS')
        os.makedirs(os.path.join(self.sound_dir, 'stream'))
        archive, self.expected = make_archive()
        with open(os.path.join(self.sound_dir, 'MY2.brsar'), 'wb') as handle:
            handle.write(archive)
        self.stream, self.stream_expected = synth.build_brstm([[0] * 60, [0] * 60], 32000, block_size=16)
        with open(os.path.join(self.sound_dir, 'stream', 'bgm_test.brstm'), 'wb') as handle:
            handle.write(self.stream)

    def _paths(self):
        return sorted(os.path.relpath(os.path.join(r, f), self.out).replace(os.sep, '/')
                      for r, _, files in os.walk(self.out) for f in files)

    def test_layout_index_and_contents(self):
        index = export_sounds.export(self.sound_dir, self.out)
        self.assertEqual(self._paths(), ['group_test/_unreferenced/file0_wave1.wav', 'group_test/chord_b.wav',
                                         'group_test/hit_a.wav', 'index.json', 'stream/bgm_test.wav'])
        rate, channels = read_wav(os.path.join(self.out, 'group_test', 'hit_a.wav'))
        self.assertEqual((rate, channels), (32000, self.expected['hit_a']))
        rate, channels = read_wav(os.path.join(self.out, 'group_test', 'chord_b.wav'))
        self.assertEqual((rate, channels), (44100, self.expected['chord_b']))
        rate, channels = read_wav(os.path.join(self.out, 'stream', 'bgm_test.wav'))
        self.assertEqual((rate, channels), (32000, self.stream_expected))
        with open(os.path.join(self.out, 'index.json'), encoding='utf-8') as handle:
            self.assertEqual(json.load(handle), index)
        by_name = {s['name']: s for s in index['sounds']}
        self.assertEqual([s['name'] for s in index['sounds']], ['bgm_test', 'hit_a', 'seq_test', 'chord_b', 'file0_wave1'])
        self.assertEqual(by_name['hit_a'], {'id': 1, 'name': 'hit_a', 'type': 'wave', 'file': 0, 'wave': 0,
                                            'group': 'group_test', 'player': 'PLAYER_se00', 'volume': 107,
                                            'priority': 64, 'rate': 32000, 'channels': 1, 'samples': 50,
                                            'seconds': 0.002, 'loop': None, 'path': 'group_test/hit_a.wav'})
        self.assertEqual(by_name['bgm_test']['source'], 'stream/bgm_test.brstm')
        self.assertEqual((by_name['bgm_test']['path'], by_name['bgm_test']['channels'], by_name['bgm_test']['samples']),
                         ('stream/bgm_test.wav', 2, 60))
        self.assertEqual((by_name['seq_test']['type'], by_name['seq_test']['bank'], by_name['seq_test']['path']),
                         ('sequence', 'bnk_test', None))
        self.assertEqual((by_name['file0_wave1']['id'], by_name['file0_wave1']['loop']), (None, {'start': 15, 'end': 29}))
        self.assertEqual(index['groups'], [{'index': 0, 'name': 'group_test', 'files': [2, 0], 'wave_bytes': mock.ANY}])
        self.assertEqual([p['name'] for p in index['players']], ['PLAYER_se00', 'PLAYER_BGM'])

    def test_missing_stream_file_is_a_warning(self):
        os.remove(os.path.join(self.sound_dir, 'stream', 'bgm_test.brstm'))
        with mock.patch.object(export_sounds._slogger, 'warning') as warning:
            index = export_sounds.export(self.sound_dir, self.out)
        self.assertTrue(any('bgm_test.brstm' in str(call.args[0]) for call in warning.call_args_list))
        self.assertIsNone(next(s for s in index['sounds'] if s['name'] == 'bgm_test')['path'])
        self.assertNotIn('stream/bgm_test.wav', self._paths())

    def test_earlier_export_is_replaced_but_other_folders_are_kept(self):
        export_sounds.export(self.sound_dir, self.out)
        stale = os.path.join(self.out, 'group_old', 'gone.wav')
        os.makedirs(os.path.dirname(stale))
        open(stale, 'wb').close()
        export_sounds.export(self.sound_dir, self.out)
        self.assertNotIn('group_old/gone.wav', self._paths())
        other = os.path.join(self.tmp, 'mine')
        os.makedirs(other)
        open(os.path.join(other, 'keep.txt'), 'w').close()
        export_sounds.clear_output(other)
        self.assertTrue(os.path.exists(os.path.join(other, 'keep.txt')))

    def test_missing_archive(self):
        with self.assertRaises(export_sounds.ExportSoundsError):
            export_sounds.export(os.path.join(self.tmp, 'nowhere'), self.out)
        self.assertEqual(export_sounds.main(['--sound-dir', os.path.join(self.tmp, 'nowhere'), '--out', self.out]), 1)

    def test_main(self):
        self.assertEqual(export_sounds.main(['--sound-dir', self.sound_dir, '--out', self.out]), 0)
        self.assertTrue(os.path.isfile(os.path.join(self.out, 'index.json')))


class DispatcherTests(unittest.TestCase):
    def test_export_sounds_flag(self):
        with mock.patch.object(sys, 'argv', ['start.py', '--export-sounds']):
            self.assertTrue(start.parse_args().export_sounds)

    @mock.patch('start.subprocess.run')
    def test_command(self, mock_run):
        mock_run.return_value.returncode = 0
        self.assertTrue(start.run_export_sounds())
        self.assertEqual(mock_run.call_args.args[0][1:], [start.SOUND_EXPORT_SCRIPT])
        mock_run.return_value.returncode = 1
        self.assertFalse(start.run_export_sounds())


if __name__ == '__main__':
    unittest.main()
