"""Music replacement: ``Sound/brstm_write`` (coefficient training, block-parallel encoding, the BRSTM writer,
WAV intake), ``clips.read_wav_frames`` and the Music tab's track reading (``gui_music.read_tracks``)."""

import os
import pathlib
import struct
import sys
import tempfile
import unittest

import numpy as np

TESTS_DIR = pathlib.Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
ROOT_DIR = TOOLS_DIR.parent
for _p in (TOOLS_DIR, ROOT_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from Sound import brstm, brstm_write, clips, dsp_adpcm, wavfile    # noqa: E402
import synthetic_sound as synth                                     # noqa: E402

BLOCK = 64                                    # bytes per channel and block: 112 samples, many blocks per test


def music(n, rate=32000, seed=1):
    """A test signal: two tones and a little noise, int16."""
    t = np.arange(n) / rate
    rng = np.random.default_rng(seed)
    x = 0.4 * np.sin(2 * np.pi * 220 * t) + 0.2 * np.sin(2 * np.pi * 1375 * t + 1) + 0.02 * rng.standard_normal(n)
    return brstm_write.to_int16(x)


def snr(ref, got):
    ref, got = np.asarray(ref, float), np.asarray(got, float)
    return 10 * np.log10((ref ** 2).sum() / max(((ref - got) ** 2).sum(), 1e-9))


def stock(n=1000, looped=False, channels=2, rate=32000):
    data, _ = synth.build_brstm([[0] * n] * channels, rate, block_size=BLOCK, looped=looped)
    return data


class CoefficientTests(unittest.TestCase):
    def test_trained_pairs_beat_no_prediction(self):
        x = music(20000)
        coefs = brstm_write.train_coefs(x)
        self.assertEqual(len(coefs), 16)
        self.assertTrue(all(-8192 <= c < 8192 for c in coefs))
        trained = dsp_adpcm.decode([dsp_adpcm.Lane(dsp_adpcm.encode(x, coefs)[0], len(x), coefs)])[0]
        zero = dsp_adpcm.decode([dsp_adpcm.Lane(dsp_adpcm.encode(x, [0] * 16)[0], len(x), [0] * 16)])[0]
        self.assertGreater(snr(x, trained), snr(x, zero) + 6)

    def test_silence_and_short_input(self):
        self.assertEqual(brstm_write.train_coefs(np.zeros(5000, np.int16)), [0] * 16)
        self.assertEqual(brstm_write.train_coefs(np.ones(20, np.int16)), [0] * 16)


class EncodeTests(unittest.TestCase):
    def test_channel_blocks_join_without_a_seam(self):
        x = music(3000)
        coefs = brstm_write.train_coefs(x)
        data, histories = brstm_write.encode_channel(x, coefs, 112)
        flat = data.reshape(-1).tobytes()
        continuous = dsp_adpcm.decode([dsp_adpcm.Lane(flat, len(x), coefs)])[0]
        self.assertGreater(snr(x, continuous), 25)
        for k, (h1, h2) in enumerate(histories):                 # every block starts where the one before ends
            s = k * 112
            self.assertEqual((h1, h2), (int(continuous[s - 1]) if s else 0, int(continuous[s - 2]) if s > 1 else 0))

    def test_pruned_search_is_close_to_the_full_one(self):
        x = music(1400, seed=4)
        coefs = brstm_write.train_coefs(x)
        full = dsp_adpcm.decode([dsp_adpcm.Lane(dsp_adpcm.encode(x, coefs)[0], len(x), coefs)])[0]
        data, _ = brstm_write.encode_channel(x, coefs, 1400 // 14 * 14)
        fast = dsp_adpcm.decode([dsp_adpcm.Lane(data.reshape(-1).tobytes(), len(x), coefs)])[0]
        self.assertGreater(snr(x, fast), snr(x, full) - 0.5)


class BuildTests(unittest.TestCase):
    def test_layout_follows_the_stock_stream(self):
        n = 1234
        channels = [music(n), music(n, seed=2)]
        out = brstm_write.build(stock(), channels, None)
        stream = brstm.parse(out)
        self.assertEqual((stream.codec, stream.rate, stream.channels, stream.samples, stream.loop),
                         (brstm.ADPCM, 32000, 2, n, None))
        self.assertEqual(stream.block_samples, 112)
        self.assertEqual(len(out) % 0x20, 0)
        self.assertEqual(struct.unpack_from('>I', out, 8)[0], len(out))
        for c, got in enumerate(brstm.decode(stream)):           # blockwise decode, from the ADPC histories
            self.assertEqual(len(got), n)
            self.assertGreater(snr(channels[c], got), 25)

    def test_loop_points_and_loop_history(self):
        n = 1500
        channels = [music(n), music(n, seed=3)]
        out = brstm_write.build(stock(looped=True), channels, (300, 1400))
        stream = brstm.parse(out)
        self.assertEqual(stream.loop, (294, 1400))                 # start on a frame, end cuts the stream
        self.assertEqual(stream.samples, 1400)
        decoded = brstm.decode(stream)
        b = out
        base = struct.unpack_from('>I', b, 0x10)[0] + 8
        ct = base + struct.unpack_from('>I', b, base + 0x14)[0]
        for c in range(2):
            ao = base + struct.unpack_from('>I', b, base + struct.unpack_from('>I', b, ct + 8 + 8 * c)[0] + 4)[0]
            ps, h1, h2, loop_ps, loop_h1, loop_h2 = struct.unpack_from('>Hhh Hhh', b, ao + 0x22)
            data_start = stream.lanes[c][0].data
            self.assertEqual((ps, h1, h2), (data_start[0], 0, 0))
            frame = 294 // 14
            block, offset = divmod(frame, 112 // 14)
            self.assertEqual(loop_ps, stream.lanes[c][block].data[offset * 8])
            self.assertEqual((loop_h1, loop_h2), (int(decoded[c][293]), int(decoded[c][292])))

    def test_refuses_other_channel_counts(self):
        with self.assertRaises(brstm_write.MusicError):
            brstm_write.build(stock(), [music(500)], None)


class WavIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _wav(self, rate, channels, loop=None, name='in.wav'):
        path = os.path.join(self.tmp.name, name)
        wavfile.write_wav(path, rate, channels, loop)
        return path

    def test_read_wav_frames_keeps_channels_and_loop(self):
        left, right = music(800), music(800, seed=9)
        rate, x, loop = clips.read_wav_frames(self._wav(22050, [left, right], (100, 700)))
        self.assertEqual((rate, x.shape, loop), (22050, (800, 2), (100, 700)))
        self.assertTrue(np.allclose(x[:, 1] * 32768, right))
        self.assertEqual(clips.read_wav(self._wav(22050, [left, right]))[1].shape, (800,))

    def test_mono_44k_into_a_stereo_32k_stream(self):
        x = music(4410, rate=44100)
        out, notes = brstm_write.encode_wav(self._wav(44100, [x]), stock(looped=True))
        stream = brstm.parse(out)
        self.assertEqual((stream.channels, stream.rate, stream.loop), (2, 32000, (0, 3200)))
        self.assertTrue(any('mono' in n for n in notes) and any('resampled' in n for n in notes))
        left, right = brstm.decode(stream)
        self.assertTrue(np.array_equal(left, right))
        expected = brstm_write.to_int16(brstm_write.resample(x[:, None] / 32767, 44100, 32000)[:, 0])
        self.assertGreater(snr(expected, left), 25)

    def test_wav_loop_wins_and_unlooped_stock_stays_unlooped(self):
        x = music(2000)
        out, _ = brstm_write.encode_wav(self._wav(32000, [x, x], (700, 1900)), stock())
        self.assertEqual(brstm.parse(out).loop, (700, 1900))
        out, _ = brstm_write.encode_wav(self._wav(32000, [x, x], name='plain.wav'), stock())
        self.assertIsNone(brstm.parse(out).loop)

    def test_resample_length_and_tone(self):
        t = np.arange(44100) / 44100
        x = np.sin(2 * np.pi * 440 * t)[:, None]
        y = brstm_write.resample(x, 44100, 32000)
        self.assertEqual(y.shape, (32000, 1))
        ref = np.sin(2 * np.pi * 440 * np.arange(32000) / 32000)
        self.assertLess(np.abs(y[2000:-2000, 0] - ref[2000:-2000]).max(), 0.01)


class TrackReadingTests(unittest.TestCase):
    def test_output_first_and_replaced_flag(self):
        import gui_music
        from test_sound_export import make_archive
        archive, _ = make_archive()
        with tempfile.TemporaryDirectory() as root:
            stream = stock()
            for top in ('1_Input', '3_Output_Dat'):
                os.makedirs(os.path.join(root, top, 'sound_NA', 'stream'))
            with open(os.path.join(root, '1_Input', 'sound_NA', 'MY2.brsar'), 'wb') as f:
                f.write(archive)
            with open(os.path.join(root, '1_Input', 'sound_NA', 'stream', 'bgm_test.brstm'), 'wb') as f:
                f.write(stream)
            tracks, archive_path = gui_music.read_tracks(root)
            self.assertTrue(archive_path.startswith(os.path.join(root, '1_Input')))
            self.assertEqual([(t.name, t.replaced, t.output) for t in tracks], [('bgm_test', False, None)])
            self.assertEqual(tracks[0].info.samples, 1000)
            with open(os.path.join(root, '3_Output_Dat', 'sound_NA', 'MY2.brsar'), 'wb') as f:
                f.write(archive)
            output = os.path.join(root, '3_Output_Dat', 'sound_NA', 'stream', 'bgm_test.brstm')
            with open(output, 'wb') as f:
                f.write(stream)
            tracks, archive_path = gui_music.read_tracks(root)
            self.assertTrue(archive_path.startswith(os.path.join(root, '3_Output_Dat')))
            self.assertEqual((tracks[0].path, tracks[0].replaced), (output, False))
            with open(output, 'wb') as f:
                f.write(brstm_write.build(stream, [music(500), music(500)], None))
            tracks, _ = gui_music.read_tracks(root)
            self.assertEqual((tracks[0].replaced, tracks[0].info.samples), (True, 500))


if __name__ == '__main__':
    unittest.main()
