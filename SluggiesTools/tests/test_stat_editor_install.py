"""The GUI's Stat Editor tab without Dear PyGui: finding the deployed editor, versions, the bridge files, the update
check, the remembered setting and the stat edits' confirm dialog."""

import json
import os
import tempfile
import unittest
from decimal import Decimal

from SluggiesTools import gui_grid, gui_settings
from SluggiesTools.Roster import slot_plan
from SluggiesTools.StatEditor import editor_install as ei


def touch(path, data=b''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


class TempDir(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())

    def release(self, name, bridge=True):
        folder = os.path.join(self.root, name)
        touch(os.path.join(folder, ei.EXE))
        if bridge:
            os.makedirs(os.path.join(folder, ei.BRIDGE_FOLDER), exist_ok=True)
        return folder


class VersionTests(unittest.TestCase):
    def test_versions_are_decimals(self):
        self.assertEqual(ei.parse_version('v4.3'), Decimal('4.3'))
        self.assertEqual(ei.parse_version('sluggers-stat-editor-v4.02'), Decimal('4.02'))
        self.assertLess(ei.parse_version('v4.02'), ei.parse_version('v4.1'))     # not (4, 2) > (4, 1)
        self.assertIsNone(ei.parse_version('latest'))
        self.assertIsNone(ei.parse_version(None))

    def test_latest_release(self):
        release = ei.parse_latest_release(json.dumps({'tag_name': 'v4.3', 'html_url': 'https://x/v4.3'}))
        self.assertEqual((release.version, release.tag, release.page), (Decimal('4.3'), 'v4.3', 'https://x/v4.3'))
        self.assertEqual(ei.parse_latest_release({'tag_name': 'v5.0'}).page, ei.RELEASES_PAGE)
        with self.assertRaises(ValueError):
            ei.parse_latest_release({'tag_name': 'nightly'})

    def test_update_text(self):
        latest = ei.Release(Decimal('4.1'), 'v4.1', 'page')
        old = ei.Install('x', ei.RELEASE, Decimal('4.02'))
        self.assertTrue(ei.update_text(old, latest)[1])
        self.assertFalse(ei.update_text(ei.Install('x', ei.RELEASE, Decimal('4.1')), latest)[1])
        text, newer = ei.update_text(ei.Install('x', ei.SOURCE_KIND, None), latest)
        self.assertFalse(newer)
        self.assertIn('unknown', text)


class FindInstallTests(TempDir):
    def test_highest_version_folder_wins(self):
        for name in ('sluggers-stat-editor-v4.02', 'sluggers-stat-editor-v4.1', 'sluggers-stat-editor-v3.6'):
            self.release(name)
        os.makedirs(os.path.join(self.root, 'sluggers-stat-editor-v9.9'))        # no exe: not a release
        found = ei.find_install(self.root)
        self.assertEqual((found.kind, found.version), (ei.RELEASE, Decimal('4.1')))
        self.assertTrue(found.bridge_mode)

    def test_release_without_bridge_is_standalone_only(self):
        folder = self.release('sluggers-stat-editor-v4.3', bridge=False)
        self.assertFalse(ei.find_install(folder).bridge_mode)
        touch(os.path.join(folder, ei.SOURCE_CODE, ei.BRIDGE_MODULE))           # the release's module copy counts
        self.assertTrue(ei.find_install(folder).bridge_mode)

    def test_source_folder_and_file_picks(self):
        checkout = os.path.join(self.root, 'Sluggers-Stat-Editor')
        touch(os.path.join(checkout, ei.SOURCE))
        found = ei.find_install(os.path.join(checkout, ei.SOURCE))                # a file stands for its folder
        self.assertEqual((found.folder, found.kind, found.version), (checkout, ei.SOURCE_KIND, None))
        self.assertFalse(found.bridge_mode)
        touch(os.path.join(checkout, ei.BRIDGE_MODULE))                           # Bridge/ itself may not exist yet
        self.assertTrue(ei.find_install(checkout).bridge_mode)
        self.assertEqual(found.bridge_path, os.path.join(checkout, 'Bridge', 'stat_bridge.json'))
        self.assertEqual(found.command(['py']), ['py', os.path.join(checkout, ei.SOURCE)])
        self.assertIsNone(found.command(None))

    def test_dist_folder_prefers_the_exe(self):
        dist = self.release('dist')
        touch(os.path.join(dist, ei.SOURCE_CODE, ei.SOURCE))
        found = ei.find_install(dist)
        self.assertEqual((found.kind, found.version), (ei.RELEASE, None))
        self.assertEqual(found.command(None), [os.path.join(dist, ei.EXE)])

    def test_nothing_there(self):
        self.assertIsNone(ei.find_install(self.root))
        self.assertIsNone(ei.find_install(os.path.join(self.root, 'missing')))
        self.assertIsNone(ei.find_install(''))

    def test_setting_keeps_the_parent_of_a_version_folder(self):
        folder = self.release('sluggers-stat-editor-v4.3')
        self.assertEqual(ei.setting_for(os.path.join(folder, ei.EXE)), self.root)
        dist = self.release('dist')
        self.assertEqual(ei.setting_for(os.path.join(dist, ei.EXE)), dist)

    def test_python_command(self):
        self.assertEqual(ei.python_command(frozen=True, which=lambda n: {'py': 'C:/py.exe'}.get(n)),
                         ['C:/py.exe', '-3'])
        self.assertEqual(ei.python_command(frozen=True, which=lambda n: '/bin/' + n if n == 'python' else None),
                         ['/bin/python'])
        self.assertIsNone(ei.python_command(frozen=True, which=lambda n: None))


class BridgeFileTests(TempDir):
    def setUp(self):
        super().setUp()
        self.install = ei.find_install(self.release('sluggers-stat-editor-v4.3'))
        self.staging = os.path.join(self.root, 'staging')

    def test_take_edits_moves_the_file(self):
        touch(self.install.edits_path, b'{"x": 1}')
        touch(self.install.bridge_path, b'{}')
        path = ei.take_edits(self.install, self.staging, now=0)
        self.assertTrue(os.path.basename(path).startswith(ei.RECEIVED_PREFIX))
        self.assertFalse(os.path.exists(self.install.edits_path))
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), b'{"x": 1}')
        self.assertTrue(os.path.exists(self.install.bridge_path))                # the caller removes the bridge
        touch(self.install.edits_path, b'{}')
        self.assertNotEqual(ei.take_edits(self.install, self.staging, now=0), path)  # same second: a new name
        self.assertIsNone(ei.take_edits(self.install, self.staging))

    def test_remove_bridge_only_removes_the_file(self):
        self.assertFalse(ei.remove_bridge(self.install))
        os.makedirs(self.install.bridge_path)                                     # a folder of that name stays
        self.assertFalse(ei.remove_bridge(self.install))
        os.rmdir(self.install.bridge_path)
        touch(self.install.bridge_path)
        touch(self.install.edits_path)
        self.assertTrue(ei.remove_bridge(self.install))
        self.assertTrue(os.path.exists(self.install.edits_path))

    def test_prune_keeps_what_is_used(self):
        kept, dropped, other = (os.path.join(self.staging, n) for n in
                                ('stat_edits_1.json', 'stat_edits_2.json', 'editor.log'))
        for path in (kept, dropped, other):
            touch(path)
        self.assertEqual(ei.prune_received(self.staging, {kept}), [dropped])
        self.assertTrue(os.path.exists(kept) and os.path.exists(other))


class SettingsTests(TempDir):
    def test_round_trip_and_broken_file(self):
        path = os.path.join(self.root, '_gui', 'settings.json')
        settings = gui_settings.Settings(path)
        self.assertIsNone(settings.get(ei.SETTING))
        settings.set(ei.SETTING, 'E:/editor')
        self.assertEqual(gui_settings.Settings(path).get(ei.SETTING), 'E:/editor')
        touch(path, b'not json')
        self.assertEqual(gui_settings.Settings(path).data, {})


def plan(edits=(), skipped=(), refused=()):
    return {'action': 'batch', 'ok': not refused, 'rebuild': False, 'commands': [], 'notes': [], 'warnings': [],
            'edits': list(edits), 'skipped': list(skipped), 'refused': list(refused), 'merged': []}


def section(path, summary='3 values on 2 characters', notes=('0x66 Fire Mario, stats: stamina 120',),
            warnings=()):
    return {'action': 'stat_edits', 'target': '0xFF', 'rebuild': False, 'commands': [], 'notes': list(notes),
            'warnings': list(warnings), 'nothing': False, 'effects': {'stat_edits': summary},
            'edit': {'op': 'stat_edits', 'id': '0xFF', 'file': path}, 'checked': False}


class StatEditsDialogTests(unittest.TestCase):
    PATH = os.path.abspath('stat_edits_1.json')

    def test_constants_match_the_planner(self):
        self.assertEqual((gui_grid.STAT_EDITS, gui_grid.GAME_WIDE), (slot_plan.STAT_EDITS, slot_plan.GAME_WIDE))
        self.assertEqual(slot_plan.parse_edits({'edits': [gui_grid.stat_edit(self.PATH)]})[0].file, self.PATH)

    def test_staged_section(self):
        dialog = gui_grid.stat_edits_dialog(None, plan([section('other.json', 'x'), section(self.PATH)],), 0, '',
                                            self.PATH)
        self.assertTrue(dialog.can_apply)
        self.assertIn(('Changes: 3 values on 2 characters', gui_grid.OK), dialog.lines)
        self.assertIn(('- 0x66 Fire Mario, stats: stamina 120', gui_grid.TEXT), dialog.lines)

    def test_nothing_to_change(self):
        skipped = dict(section(self.PATH), nothing=True, notes=['nothing to change'])
        dialog = gui_grid.stat_edits_dialog(None, plan(skipped=[skipped]), 0, '', self.PATH)
        self.assertFalse(dialog.can_apply)
        self.assertEqual(dialog.title, 'Stat edits: nothing to change')

    def test_refused_for_another_main_dol(self):
        refused = {'edit': gui_grid.stat_edit(self.PATH), 'target': '0xFF',
                   'error': 'x.json was made for another state of main.dol (the game was patched ...)'}
        dialog = gui_grid.stat_edits_dialog(None, plan(refused=[refused]), 1, '', self.PATH)
        self.assertFalse(dialog.can_apply)
        texts = [t for t, _k in dialog.lines]
        self.assertTrue(texts[0].startswith('Refused: x.json was made'))
        self.assertFalse(any('pending edit no longer fits' in t for t in texts))

    def test_failed_check(self):
        dialog = gui_grid.stat_edits_dialog(None, plan([section(self.PATH)]), 1, '[Error] boom', self.PATH)
        self.assertFalse(dialog.can_apply)
        self.assertIn(('Check failed: boom. Nothing was staged.', gui_grid.ERROR), dialog.lines)

    def test_staged_files_in_order(self):
        pending = gui_grid.PendingEdits()
        pending.edits = [{'op': 'rename', 'id': '0x00', 'text': 'A'}, gui_grid.stat_edit('a.json'),
                         gui_grid.stat_edit('b.json')]
        self.assertEqual(gui_grid.staged_stat_files(pending), ['a.json', 'b.json'])
        pending.discard(gui_grid.GAME_WIDE)
        self.assertEqual(gui_grid.staged_stat_files(pending), [])
        self.assertEqual(len(pending), 1)


if __name__ == '__main__':
    unittest.main()
