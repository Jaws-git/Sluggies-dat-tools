"""``state_cli.derive_for_write``: custom voice clips that cannot be read back are never dropped silently."""

import os
import sys
import unittest
from unittest import mock

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)            # Sound's modules import their siblings as ``Sound.x``

from SluggiesTools.Roster import derive, state_cli  # noqa: E402


def _derived(lost):
    return derive.Derived({}, warnings=[f'{w}: custom clip dropped' for w in lost], lost_clips=list(lost))


class DeriveForWriteTests(unittest.TestCase):
    LOST = ['character_voices 0x66.v3', 'species_clips 0x0D.v1']

    def run_guard(self, lost, sound=(None, 'MY2.brsar is missing', False), dry_run=False, answer=None, env=None):
        asked = []

        def ask(prompt, kind=None):
            asked.append((prompt, kind))
            if answer is None:
                raise EOFError
            return answer
        environ = {k: v for k, v in os.environ.items() if k != state_cli.DROP_CLIPS_ENV}
        environ.update(env or {})
        with mock.patch.object(state_cli, 'read_sound', return_value=sound), \
                mock.patch.object(state_cli.derive, 'derive', return_value=_derived(lost)), \
                mock.patch.object(state_cli.slogger, 'ask', side_effect=ask), \
                mock.patch.object(state_cli.slogger, 'warning'), \
                mock.patch.dict(os.environ, environ, clear=True):
            return state_cli.derive_for_write(None, None, 'out', dry_run), asked

    def test_nothing_lost_never_asks(self):
        derived, asked = self.run_guard([], sound=(None, 'MY2.brsar is missing', False))
        self.assertEqual((derived.lost_clips, asked), ([], []))

    def test_no_answer_refuses(self):
        with self.assertRaises(derive.DeriveError) as caught:
            self.run_guard(self.LOST)
        self.assertIn('--drop-voice-clips', str(caught.exception))
        self.assertIn('2 custom voice clip(s)', str(caught.exception))

    def test_no_refuses_and_yes_goes_on(self):
        with self.assertRaises(derive.DeriveError):
            self.run_guard(self.LOST, answer='n')
        derived, asked = self.run_guard(self.LOST, answer='y')
        self.assertEqual(derived.lost_clips, self.LOST)
        self.assertEqual([kind for _prompt, kind in asked], ['danger'])      # never skipped by the GUI
        self.assertIn('is missing', asked[0][0])

    def test_a_file_in_use_refuses_without_asking(self):
        for answer, env in (('y', None), (None, {state_cli.DROP_CLIPS_ENV: '1'})):
            with self.assertRaises(derive.DeriveError) as caught:
                self.run_guard(self.LOST, sound=(None, 'MY2.brsar cannot be opened (locked)', True), answer=answer,
                               env=env)
            self.assertIn('try again', str(caught.exception))

    def test_the_flag_goes_on_without_asking(self):
        derived, asked = self.run_guard(self.LOST, env={state_cli.DROP_CLIPS_ENV: '1'})
        self.assertEqual((derived.lost_clips, asked), (self.LOST, []))

    def test_a_dry_run_only_warns(self):
        derived, asked = self.run_guard(self.LOST, dry_run=True)
        self.assertEqual(asked, [])
        self.assertIn('2 custom voice clip(s)', derived.warnings[0])

    def test_single_unreadable_clips_of_a_readable_archive_ask_too(self):
        with self.assertRaises(derive.DeriveError) as caught:
            self.run_guard(self.LOST[:1], sound=(object(), None, False), answer='n')
        self.assertIn('does not hold them', str(caught.exception))


class ReadSoundTests(unittest.TestCase):
    def test_missing_and_damaged(self):
        import tempfile
        from SluggiesTools.Sound import brsar
        with tempfile.TemporaryDirectory() as tmp:
            archive, problem, retry = state_cli.read_sound(tmp)
            self.assertIsNone(archive)
            self.assertIn('is missing', problem)
            self.assertFalse(retry)
            path = os.path.join(tmp, brsar.ARCHIVE)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(b'not an archive')
            archive, problem, retry = state_cli.read_sound(tmp)
            self.assertIsNone(archive)
            self.assertIn('is damaged', problem)
            self.assertFalse(retry)
            self.assertIsNone(state_cli.open_sound(tmp))


if __name__ == '__main__':
    unittest.main()
