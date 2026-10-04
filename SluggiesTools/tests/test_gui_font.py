import os
import pathlib
import sys
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import gui


class FontSelectionTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_segment_ui_is_first_candidate_on_windows(self):
        with mock.patch.object(gui, '_WINDOWS', True), \
             mock.patch.dict(os.environ, {'SystemRoot': r'C:\Windows'}):
            candidates = gui._font_candidates()
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0][1], 'Segoe UI')
        self.assertEqual(candidates[0][0], r'C:\Windows\Fonts\segoeui.ttf')
        self.assertEqual(candidates[1][1], 'Open Sans')

    def test_bundled_open_sans_is_only_candidate_off_windows(self):
        with mock.patch.object(gui, '_WINDOWS', False):
            candidates = gui._font_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1], 'Open Sans')
        self.assertTrue(candidates[0][0].endswith(
            os.path.join('SluggiesTools', 'Roster', 'fonts', 'OpenSans.ttf')))

    def test_select_font_path_takes_the_first_existing_candidate(self):
        with mock.patch.object(gui, '_WINDOWS', True), \
             mock.patch.dict(os.environ, {'SystemRoot': r'C:\Windows'}):
            candidates = gui._font_candidates()
            segoe = candidates[0][0]
            self.assertEqual(gui._select_font_path(exists=lambda path: True),
                             segoe)

    def test_select_font_path_falls_back_to_bundled_when_segment_ui_is_absent(self):
        with mock.patch.object(gui, '_WINDOWS', True), \
             mock.patch.dict(os.environ, {'SystemRoot': r'C:\Windows'}):
            candidates = gui._font_candidates()
            segoe, opensans = candidates[0][0], candidates[1][0]
            exists = lambda path: path == opensans  # noqa: E731
            self.assertEqual(gui._select_font_path(exists=exists), opensans)

    def test_existing_font_paths_keeps_preference_order(self):
        with mock.patch.object(gui, '_WINDOWS', True), \
             mock.patch.dict(os.environ, {'SystemRoot': r'C:\Windows'}):
            expected = [path for path, _label in gui._font_candidates()]
            self.assertEqual(gui._existing_font_paths(exists=lambda path: True),
                             expected)

    def test_select_font_path_returns_none_when_nothing_exists(self):
        with mock.patch.object(gui, '_WINDOWS', True), \
             mock.patch.dict(os.environ, {'SystemRoot': r'C:\Windows'}):
            self.assertIsNone(gui._select_font_path(exists=lambda path: False))

    def test_font_size_for_segment_ui_and_open_sans(self):
        self.assertEqual(gui._font_size_for(r'C:\Windows\Fonts\segoeui.ttf'),
                         gui._FONT_SIZE_SEGOE_UI)
        self.assertEqual(gui._font_size_for('SluggiesTools/Roster/fonts/OpenSans.ttf'),
                         gui._FONT_SIZE_OPEN_SANS)
        self.assertEqual(gui._font_size_for(None), gui._FONT_SIZE_OPEN_SANS)
        self.assertEqual(gui._font_size_for('SomeOther.ttf'), gui._FONT_SIZE_OPEN_SANS)


class BundledFontPathTests(unittest.TestCase):
    """_bundled_font_path must find the TTF the same way start.py finds ROOT:
    next to the executable when frozen, next to this module in source."""

    def test_source_checkout_uses_repo_root(self):
        path = gui._bundled_font_path()
        self.assertTrue(path.startswith(str(TOOLS_DIR.parent)))
        self.assertTrue(os.path.isfile(path))

    def test_frozen_build_uses_the_executable_directory(self):
        # Built with os.path so the test holds on Windows and on Linux CI.
        release = os.path.abspath(os.path.join(os.sep, 'Release'))
        with mock.patch.object(sys, 'frozen', True, create=True), \
             mock.patch.object(sys, 'executable',
                               os.path.join(release, 'sluggies-dat-tools.exe')):
            self.assertEqual(
                gui._bundled_font_path(),
                os.path.join(release, 'SluggiesTools', 'Roster', 'fonts',
                             'OpenSans.ttf'))


def _registry_mock():
    registry = mock.MagicMock(name='font_registry')
    registry.return_value.__enter__ = mock.Mock(name='enter')
    registry.return_value.__exit__ = mock.Mock(name='exit', return_value=False)
    return registry


class SetupFontsTests(unittest.TestCase):
    """The registry + bind behaviour of _setup_fonts, with everything but the
    selection logic mocked so the tests need no display."""

    def tearDown(self):
        mock.patch.stopall()

    def _gui(self):
        return gui.SluggiesGui(['start'], 'root')

    def test_no_font_file_leaves_proggy_clean_in_place(self):
        with mock.patch.object(gui, '_existing_font_paths', return_value=[]), \
             mock.patch.object(gui, 'slogger') as log:
            self._gui()._setup_fonts()
        log.info.assert_called_once()
        self.assertIn('ProggyClean',
                      log.info.call_args.args[0] + log.info.call_args.kwargs.get('source', ''))

    def test_successful_registration_binds_the_font(self):
        registry = _registry_mock()
        with mock.patch.object(gui, '_existing_font_paths',
                               return_value=['C:/Fonts/segoeui.ttf']), \
             mock.patch.object(gui.dpg, 'font_registry', registry), \
             mock.patch.object(gui.dpg, 'add_font') as add_font, \
             mock.patch.object(gui.dpg, 'does_item_exist', return_value=True), \
             mock.patch.object(gui.dpg, 'bind_font') as bind_font, \
             mock.patch.object(gui, 'slogger') as log:
            self._gui()._setup_fonts()
        add_font.assert_called_once_with('C:/Fonts/segoeui.ttf',
                                         gui._FONT_SIZE_SEGOE_UI, tag=gui._FONT_TAG)
        bind_font.assert_called_once_with(gui._FONT_TAG)
        log.warning.assert_not_called()

    def test_failed_registration_falls_back_to_proggy_clean(self):
        registry = _registry_mock()
        with mock.patch.object(gui, '_existing_font_paths',
                               return_value=['C:/Fonts/segoeui.ttf',
                                             'sluggie/fonts/OpenSans.ttf']), \
             mock.patch.object(gui.dpg, 'font_registry', registry), \
             mock.patch.object(gui.dpg, 'add_font',
                               side_effect=SystemError('broken font file')) as add_font, \
             mock.patch.object(gui.dpg, 'does_item_exist') as does_exist, \
             mock.patch.object(gui.dpg, 'bind_font') as bind_font, \
             mock.patch.object(gui, 'slogger') as log:
            self._gui()._setup_fonts()
        self.assertEqual(add_font.call_count, 2)
        does_exist.assert_not_called()
        bind_font.assert_not_called()
        self.assertIn('ProggyClean', log.warning.call_args.args[0])

    def test_failed_segoe_ui_falls_back_to_open_sans(self):
        registry = _registry_mock()
        segoe, opensans = 'C:/Fonts/segoeui.ttf', 'sluggie/fonts/OpenSans.ttf'

        def add_font(path, size, tag):
            if path == segoe:
                raise SystemError('broken font file')

        with mock.patch.object(gui, '_existing_font_paths',
                               return_value=[segoe, opensans]), \
             mock.patch.object(gui.dpg, 'font_registry', registry), \
             mock.patch.object(gui.dpg, 'add_font', side_effect=add_font) as add, \
             mock.patch.object(gui.dpg, 'does_item_exist', return_value=True), \
             mock.patch.object(gui.dpg, 'bind_font') as bind_font, \
             mock.patch.object(gui, 'slogger') as log:
            self._gui()._setup_fonts()
        add.assert_called_with(opensans, gui._FONT_SIZE_OPEN_SANS, tag=gui._FONT_TAG)
        bind_font.assert_called_once_with(gui._FONT_TAG)
        log.warning.assert_called_once()
        self.assertIn(segoe, log.warning.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
