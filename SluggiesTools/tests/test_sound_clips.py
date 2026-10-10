"""Custom voice clips (``Sound/clips.py``): WAV intake, preparation, encoding and the RWAV checks, on synthetic
sounds only."""

import io
import os
import pathlib
import struct
import sys
import tempfile
import unittest

import numpy as np

TESTS_DIR = pathlib.Path(__file__).resolve().parent
for _p in (TESTS_DIR.parent, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from SluggiesTools.Sound import brsar, clips, wavfile  # noqa: E402
import synthetic_sound as synth  # noqa: E402


def tone(rate=44100, seconds=0.05, freq=440.0, amplitude=0.5):
    t = np.arange(int(seconds * rate)) / rate
    return np.sin(2 * np.pi * freq * t) * amplitude


def wav(samples_float, rate, channels=1, tag=1, width=2):
    """A RIFF WAVE of ``samples_float`` (-1..1) in the given PCM width or float format (``tag`` 3)."""
    x = np.asarray(samples_float)
    if tag == 3:
        data = x.astype('<f4' if width == 4 else '<f8').tobytes() if channels == 1 else \
            np.repeat(x, channels).astype('<f4' if width == 4 else '<f8').tobytes()
    elif width == 1:
        data = (np.clip(np.round(x * 127), -128, 127) + 128).astype(np.uint8).repeat(channels).tobytes()
    elif width == 2:
        data = np.clip(np.round(x * 32767), -32768, 32767).astype('<i2').repeat(channels).tobytes()
    elif width == 3:
        v = np.clip(np.round(x * (1 << 23) - 1), -(1 << 23), (1 << 23) - 1).astype(np.int32).repeat(channels)
        data = b''.join(int(s).to_bytes(3, 'little', signed=True) for s in v)
    else:
        data = np.clip(np.round(x * (1 << 31) - 1), -(1 << 31), (1 << 31) - 1).astype('<i4').repeat(channels).tobytes()
    fmt = struct.pack('<HHIIHH', tag, channels, rate, rate * width * channels, width * channels, 8 * width)
    body = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', 4 + len(body)) + b'WAVE' + body


class ReadWavTests(unittest.TestCase):
    def test_every_pcm_width_and_float(self):
        x = tone()
        for tag, width in ((1, 1), (1, 2), (1, 3), (1, 4), (3, 4), (3, 8)):
            with self.subTest(tag=tag, width=width):
                rate, got = clips.read_wav(wav(x, 44100, tag=tag, width=width))
                self.assertEqual(rate, 44100)
                self.assertEqual(len(got), len(x))
                tolerance = 1 / 100 if width == 1 else 1e-3
                self.assertLess(float(np.abs(got - x).max()), tolerance)

    def test_stereo_is_mixed_down_and_extensible_read(self):
        x = tone()
        rate, got = clips.read_wav(wav(x, 22050, channels=2))
        self.assertEqual((rate, len(got)), (22050, len(x)))
        self.assertLess(float(np.abs(got - x).max()), 1e-3)
        data = bytearray(wav(x, 22050))
        at = data.index(b'fmt ')
        fmt = struct.pack('<HHIIHH', 0xFFFE, 1, 22050, 44100, 2, 16) + struct.pack('<HHI', 22, 16, 3) + \
            struct.pack('<H', 1) + bytes(14)
        extensible = data[:at] + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + data[at + 8 + 16:]
        self.assertEqual(clips.read_wav(bytes(extensible))[0], 22050)

    def test_wav_file_and_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'a.wav')
            with open(path, 'wb') as f:
                f.write(wav(tone(), 32000))
            self.assertEqual(clips.read_wav(path)[0], 32000)
            with self.assertRaisesRegex(clips.ClipError, 'not a WAV'):
                clips.read_wav(b'RIFX' + bytes(20))
            with self.assertRaisesRegex(clips.ClipError, 'no sound'):
                clips.read_wav(wav(np.zeros(0), 32000))
            with self.assertRaisesRegex(clips.ClipError, 'cannot read'):
                clips.read_wav(wav(tone(), 32000, tag=2))
            with self.assertRaises(clips.ClipError):
                clips.read_wav(os.path.join(tmp, 'missing.wav'))


class PrepareTests(unittest.TestCase):
    def test_resample_cut_and_clip(self):
        samples, notes = clips.prepare(44100, tone(44100, 0.1))
        self.assertEqual(samples.dtype, np.int16)
        self.assertEqual(len(samples), 3200)                              # 0.1 s at 32 kHz
        self.assertTrue(any('resampled' in n for n in notes))
        same, notes = clips.prepare(clips.RATE, tone(clips.RATE, 0.1))
        self.assertEqual((len(same), notes), (3200, []))
        cut, notes = clips.prepare(clips.RATE, tone(clips.RATE, 3.0))
        self.assertEqual(len(cut), int(clips.MAX_SECONDS * clips.RATE))
        self.assertTrue(any('cut' in n for n in notes))
        loud, notes = clips.prepare(clips.RATE, tone(clips.RATE, 0.01, amplitude=2.0))
        self.assertEqual(int(loud.max()), 32767)
        self.assertTrue(any('clipped' in n for n in notes))
        with self.assertRaises(clips.ClipError):
            clips.prepare(clips.RATE, [])


class EncodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = brsar.Brsar(synth.build_voice_archive()[0])

    def test_templates_from_the_select_file(self):
        templates = clips.voice_templates(self.archive)
        self.assertEqual(len(templates), clips.TEMPLATE_COUNT)
        self.assertTrue(all(t[:4] == b'RWAV' for t in templates))
        select = next(g for g in self.archive.groups() if g.name == clips.SELECT_GROUP)
        self.assertEqual(select.items[0].file_id, 43)

    def test_encode_round_trip(self):
        x = tone(clips.RATE, 0.05, amplitude=0.4)
        samples, _ = clips.prepare(clips.RATE, x)
        rwav = clips.encode(samples, clips.voice_templates(self.archive, 2))
        wave = clips.check_rwav(rwav, 'test')
        self.assertEqual((wave.format, wave.rate, wave.samples, len(wave.channels)), (brsar.ADPCM, clips.RATE, 1600, 1))
        decoded = brsar.decode_waves([wave])[0][0].astype(np.int64)
        error = np.abs(decoded - samples.astype(np.int64)).mean()
        self.assertLess(error, 2500)                      # random synthetic coefficients: coarse, but a tone
        self.assertAlmostEqual(clips.rwav_seconds(rwav), 0.05, places=3)
        back = wavfile.wav_bytes(wave.rate, [decoded.astype(np.int16)])
        self.assertEqual(clips.rwav_wav_bytes(rwav), back)

    def test_encode_wav_and_load_clip(self):
        templates = clips.voice_templates(self.archive, 2)
        rwav, notes = clips.encode_wav(wav(tone(), 44100, channels=2), templates)
        self.assertEqual(rwav[:4], b'RWAV')
        self.assertTrue(any('resampled' in n for n in notes))
        with tempfile.TemporaryDirectory() as tmp:
            wav_path, rwav_path = os.path.join(tmp, 'a.wav'), os.path.join(tmp, 'a.rwav')
            with open(wav_path, 'wb') as f:
                f.write(wav(tone(), 44100))
            with open(rwav_path, 'wb') as f:
                f.write(rwav)
            self.assertEqual(clips.load_clip(rwav_path, None), (rwav, []))     # an RWAV needs no templates
            calls = []

            def lazy():
                calls.append(1)
                return templates
            data, notes = clips.load_clip(wav_path, lazy)
            self.assertEqual((data[:4], calls), (b'RWAV', [1]))
            with self.assertRaisesRegex(clips.ClipError, 'sound archive'):
                clips.load_clip(wav_path, lambda: [])
            with open(rwav_path, 'wb') as f:
                f.write(b'RWAV' + bytes(60))
            with self.assertRaises(clips.ClipError):
                clips.load_clip(rwav_path, None)
            with self.assertRaises(clips.ClipError):
                clips.load_clip(os.path.join(tmp, 'missing.rwav'), None)

    def test_check_rwav_refusals(self):
        with self.assertRaises(clips.ClipError):
            clips.check_rwav(b'RIFF....', 'x')
        stereo = synth.build_rwav([(synth.random_adpcm(synth.random.Random(1), 14), 14, [0] * 16, 0, 0)] * 2, 32000)
        with self.assertRaises(clips.ClipError):
            clips.check_rwav(stereo, 'x')
        pcm = synth.build_rwav([[0, 1, 2, 3]], 32000, fmt=1)
        with self.assertRaises(clips.ClipError):
            clips.check_rwav(pcm, 'x')
        self.assertTrue(clips.is_rwav_name('A.RWAV'))
        self.assertFalse(clips.is_rwav_name('a.wav'))


if __name__ == '__main__':
    unittest.main()
