"""The Music tab's playback cache without a window: one decode per sound at a time, and a decoded WAV is deleted
when it leaves the cache."""
import os
import pathlib
import queue
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import gui_music  # noqa: E402


class PlaybackCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(gui_music, 'dpg'))
        self.enterContext(mock.patch.object(gui_music, '_WINDOWS', True))
        self.threads = self.enterContext(mock.patch.object(gui_music.threading, 'Thread'))
        self.app = SimpleNamespace(output_queue=queue.Queue(), busy=False, root_dir=self.tmp,
                                   log_line=lambda *a: None)
        self.tab = gui_music.MusicTab(self.app)
        self.tab._cache_dir = self.tmp                       # no temp folder of its own, no atexit hook
        self.started = []
        self.tab._start = lambda track, path: self.started.append(path)
        self.track = gui_music.Track(sound_id=7, name='bgm', external='stream/bgm.brstm',
                                     stock=os.path.join(self.tmp, 'bgm.brstm'))

    def _wav(self, name):
        path = os.path.join(self.tmp, name)
        with open(path, 'wb') as f:
            f.write(b'RIFF')
        return path

    def test_a_second_play_joins_the_running_decode(self):
        self.tab.play(self.track)
        self.tab.play(self.track)
        self.assertEqual(self.threads.call_count, 1)
        self.assertEqual(self.tab._decoding, 1)
        key = (7, self.tab._epoch)
        self.assertEqual(self.tab._running, {key: self.tab._request})    # the later click is the one answered
        target = self._wav('7_1.wav')
        self.tab._decoded(self.track, self.track.path, target, key, None)
        self.assertEqual((self.started, self.tab._cache, self.tab._decoding, self.tab._running),
                         ([target], {7: target}, 0, {}))
        self.tab.play(self.track)                                         # cached: no decode
        self.assertEqual((self.threads.call_count, self.started), (1, [target, target]))

    def test_a_stopped_decode_is_cached_but_not_played(self):
        self.tab.play(self.track)
        self.tab._request += 1                                            # Stop
        target = self._wav('7_1.wav')
        self.tab._decoded(self.track, self.track.path, target, (7, self.tab._epoch), None)
        self.assertEqual((self.started, self.tab._cache), ([], {7: target}))

    def test_a_decode_of_a_file_replaced_meanwhile_is_deleted(self):
        self.tab.play(self.track)
        key = (7, self.tab._epoch)
        self.tab._epoch += 1                                              # Replace / Reset / Refresh
        self.tab._request += 1
        target = self._wav('7_1.wav')
        self.tab._decoded(self.track, self.track.path, target, key, None)
        self.assertEqual((self.tab._cache, os.path.exists(target)), ({}, False))

    def test_entries_that_leave_the_cache_are_deleted(self):
        first, second = self._wav('1_1.wav'), self._wav('2_2.wav')
        self.tab._cache = {1: first, 2: second}
        self.tab._read_done(None, None, 'no archive')                     # Refresh drops every entry
        self.assertEqual((self.tab._cache, os.path.exists(first), os.path.exists(second)), ({}, False, False))

    def test_a_file_that_cannot_be_deleted_yet_is_tried_again(self):
        playing, other = self._wav('1_1.wav'), self._wav('2_2.wav')
        with mock.patch.object(gui_music.os, 'remove', side_effect=PermissionError('playing')):
            self.tab._discard(playing)
        self.assertEqual(self.tab._stale, [playing])
        self.tab._discard(other)
        self.assertEqual((self.tab._stale, os.path.exists(playing), os.path.exists(other)), ([], False, False))

    def test_replace_and_reset_wait_for_a_running_command(self):
        self.app.busy = True
        self.tab.picks = mock.Mock()
        self.app.picking = False
        output = os.path.join(self.tmp, '3_Output_Dat', 'sound_NA', 'stream', 'bgm.brstm')
        self.tab._on_replace(self.track)
        self.tab._on_reset(self.track)
        self.tab._encode(self.track, 'new.wav')
        self.tab.picks.pick_sound.assert_not_called()
        self.assertFalse(os.path.exists(output))
        self.assertIsNone(self.tab.working)


if __name__ == '__main__':
    unittest.main()
