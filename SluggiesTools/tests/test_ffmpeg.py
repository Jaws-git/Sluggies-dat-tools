"""Tests for Sound/ffmpeg.py: finding ffmpeg off PATH."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from Sound import clips, ffmpeg, wavfile


class FindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {'LOCALAPPDATA': os.path.join(self.tmp.name, 'local'),
               'ProgramData': os.path.join(self.tmp.name, 'pd'),
               'SCOOP': os.path.join(self.tmp.name, 'scoop')}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _touch(self, *parts):
        path = os.path.join(self.tmp.name, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').close()
        return path

    def test_path_wins(self):
        self._touch('local', 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe')
        with mock.patch('shutil.which', return_value='X:/ffmpeg.exe'):
            self.assertEqual(ffmpeg.find(), 'X:/ffmpeg.exe')

    def test_none_found(self):
        with mock.patch('shutil.which', return_value=None):
            self.assertIsNone(ffmpeg.find())

    def test_winget_links_before_package_folder(self):
        links = self._touch('local', 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe')
        self._touch('local', 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg_x', 'ffmpeg-8.0', 'bin', 'ffmpeg.exe')
        with mock.patch('shutil.which', return_value=None):
            self.assertEqual(ffmpeg.find(), links)

    def test_winget_package_folder(self):
        path = self._touch('local', 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg_x', 'ffmpeg-8.0', 'bin',
                           'ffmpeg.exe')
        with mock.patch('shutil.which', return_value=None):
            self.assertEqual(ffmpeg.find(), path)

    def test_scoop_and_chocolatey(self):
        choco = self._touch('pd', 'chocolatey', 'bin', 'ffmpeg.exe')
        with mock.patch('shutil.which', return_value=None):
            self.assertEqual(ffmpeg.find(), choco)
        scoop = self._touch('scoop', 'shims', 'ffmpeg.exe')
        with mock.patch('shutil.which', return_value=None):
            self.assertEqual(ffmpeg.find(), scoop)


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_not_a_wav_without_ffmpeg(self):
        path = os.path.join(self.tmp.name, 'song.mp3')
        with open(path, 'wb') as handle:
            handle.write(b'ID3' + bytes(64))
        with mock.patch.object(ffmpeg, 'find', return_value=None):
            with self.assertRaisesRegex(clips.ClipError, 'needs ffmpeg'):
                clips.read_wav_frames(path)

    def test_bytes_are_never_decoded(self):
        with mock.patch.object(ffmpeg, 'decode_to_wav') as decode:
            with self.assertRaisesRegex(clips.ClipError, 'not a WAV'):
                clips.read_wav_frames(b'fLaC' + bytes(64))
        decode.assert_not_called()

    @unittest.skipUnless(ffmpeg.find(), 'ffmpeg is not installed')
    def test_flac_round_trip(self):
        rate = 22050
        t = np.arange(rate // 2) / rate
        left = np.round(np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
        right = np.round(np.sin(2 * np.pi * 660 * t) * 8000).astype(np.int16)
        wav = os.path.join(self.tmp.name, 'tone.wav')
        flac = os.path.join(self.tmp.name, 'tone.flac')
        with open(wav, 'wb') as handle:
            handle.write(wavfile.wav_bytes(rate, [left, right]))
        import subprocess
        subprocess.run([ffmpeg.find(), '-nostdin', '-v', 'error', '-y', '-i', wav, flac], check=True)
        got_rate, x, loop = clips.read_wav_frames(flac)
        self.assertEqual(got_rate, rate)
        self.assertEqual(x.shape, (len(left), 2))
        self.assertIsNone(loop)
        np.testing.assert_allclose(x[:, 0], left / 32768, atol=1e-6)     # FLAC is lossless
        np.testing.assert_allclose(x[:, 1], right / 32768, atol=1e-6)

    @unittest.skipUnless(ffmpeg.find(), 'ffmpeg is not installed')
    def test_unreadable_file(self):
        path = os.path.join(self.tmp.name, 'broken.ogg')
        with open(path, 'wb') as handle:
            handle.write(b'not sound at all' * 8)
        with self.assertRaisesRegex(clips.ClipError, 'ffmpeg could not read broken.ogg'):
            clips.read_wav_frames(path)


if __name__ == '__main__':
    unittest.main()
