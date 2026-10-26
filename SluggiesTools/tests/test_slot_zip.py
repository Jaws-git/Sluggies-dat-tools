"""Character packages (``Roster/slot_zip.py``): the manifest, the stat file made from a snapshot, staging a package
onto a slot (aspects present, missing, refused for the slot), the voice pack it carries, and the grid's dialog.

No production data: packages are written by hand into a temporary folder.
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest
import zipfile

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
for _path in (TOOLS_DIR, TOOLS_DIR / 'Roster', pathlib.Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import gui_grid  # noqa: E402
import synthetic_sound  # noqa: E402
from Roster import slot_plan, slot_zip  # noqa: E402
from Sound import sound_packs  # noqa: E402
from StatEditor import fields  # noqa: E402

HIGH = 'files/78277664_mario.gpl/78277664_mario_Fire01.gpl.sluggie'
LOW = 'files/78711424_L_mario.gpl/78711424_L_mario_Fire01.gpl.sluggie'
BAT = 'files/78789408/78789440_bat.gpl/78789440_bat_Fire01.gpl.sluggie'
GLOVE = 'files/78798560/78798592_glove_L.tpl/78798592_glove_L_Fire01.tpl.sluggie'
BANK = 'files/anm/Fire_7_Batting.anm'
FRONT, SIDE = 'files/78277664_mario.gpl/icon/FrontIcon.png', 'files/78277664_mario.gpl/icon/SideIcon.png'
SHA1 = 'ab' * 20


def _field(group):
    return next(f for f in fields.CHARACTER_FIELDS if f.group == group)


def snapshot():
    """Two fields (one per kind of group), and chemistry: own pair, a stock partner, the target, a new ID."""
    size, stat = _field('size'), _field('stats')
    return {'fields': {'size': {size.name: size.pack(1.25).hex()}, 'stats': {stat.name: stat.pack(
                stat.choices[0] if stat.choices else 1).hex()}},
            'chemistry': {'0x00': [2, 2], '0x09': [0, 1], '0x66': [1, 1], '0x70': [2, 2]}}


def manifest(**over):
    doc = {'format': slot_zip.FORMAT, 'version': 1,
           'character': {'id': '0x00', 'name': 'Fire Mario', 'exported': '2026-10-26'},
           'name': 'Fire Mario', 'model': {'high': HIGH, 'low': LOW},
           'equipment': {'2': {'file': BAT, 'changed': True}, '3': {'file': GLOVE, 'changed': False}},
           'animations': {'7': {'file': BANK, 'changed': True}},
           'portraits': {'front': FRONT, 'side': SIDE},
           'stats': {'source': '0x00', 'values': slot_zip.STATS_FILE},
           'voice': {'source': '0x00', 'own': True, 'pack': None}}
    doc.update(over)
    return doc


def write_package(folder, doc, leave_out=(), extra=None):
    path = os.path.join(folder, 'Fire Mario.sluggiezip')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(slot_zip.MANIFEST, json.dumps(doc))
        for name in (HIGH, LOW, BAT, GLOVE, BANK, FRONT, SIDE):
            if name not in leave_out:
                zf.writestr(name, b'x')
        if slot_zip.STATS_FILE not in leave_out:
            zf.writestr(slot_zip.STATS_FILE, json.dumps(snapshot()))
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


def state(cid=0x66, voice=0x09, char_voice=None):
    return {'characters': [{'id': cid, 'voice': voice, 'char_voice': char_voice}],
            'sizes': {'main_dol_sha1': SHA1}}


def rwav():
    rng = synthetic_sound.random.Random(1)
    return synthetic_sound.build_rwav([(synthetic_sound.random_adpcm(rng, 28), 28, [0] * 16, 0, 0)], 32000)


class ManifestTests(unittest.TestCase):
    def test_manifest_of_a_slot(self):
        char = {'id': 0x66, 'name': {'en': 'Fire Mario'}, 'stats': 0x00,
                'equipment': {'bat': {'file': 2, 'vanilla': True}, 'glove_l': {'file': 3, 'vanilla': None}},
                'animations': [{'file': 6, 'vanilla': True}, {'file': 7, 'vanilla': False}]}
        record = {0: [os.path.join('a.gpl', 'a_F01.gpl.sluggie')], 1: [os.path.join('L_a.gpl', 'L_a_F01.gpl.sluggie')],
                  2: ['bat.sluggie'], 3: ['glove.sluggie'], 6: [os.path.join('anm', 'F_6.anm')], 7: ['anm/F_7.anm'],
                  'portraits': {'front': os.path.join('a.gpl', 'icon', 'FrontIcon.png')}}
        doc = slot_zip.manifest_of(char, record, {'fields': {}}, {'source': '0x09', 'own': True, 'clips': {'v1': b''}},
                                   '2026-10-26')
        self.assertEqual(doc['model'], {'high': 'files/a.gpl/a_F01.gpl.sluggie', 'low': 'files/L_a.gpl/L_a_F01.gpl.sluggie'})
        self.assertEqual(doc['equipment'], {'2': {'file': 'files/bat.sluggie', 'changed': False},
                                            '3': {'file': 'files/glove.sluggie', 'changed': True}})   # unknown: changed
        self.assertEqual({k: v['changed'] for k, v in doc['animations'].items()}, {'6': False, '7': True})
        self.assertEqual(doc['portraits'], {'front': 'files/a.gpl/icon/FrontIcon.png'})
        self.assertEqual(doc['stats'], {'source': '0x00', 'values': 'stats.json'})
        self.assertEqual(doc['voice'], {'source': '0x09', 'own': True, 'pack': 'voice.sluggiesvoice'})
        self.assertEqual(doc['name'], 'Fire Mario')
        unnamed = slot_zip.manifest_of(dict(char, name={'en': '-'}), record, None, None, '2026-10-26')
        self.assertIsNone(unnamed['name'])
        self.assertNotIn('stats', unnamed)
        self.assertNotIn('voice', unnamed)

    def test_voice_of(self):
        self.assertEqual(slot_zip.voice_of({'id': 1, 'char_voice': None, 'voice': 0x09}, None),
                         {'source': '0x09', 'own': False, 'clips': {}, 'pack': None})
        self.assertEqual(slot_zip.voice_of({'id': 1, 'char_voice': 0x0B, 'voice': 0x09}, None)['source'], '0x0B')

        class Derived:
            config = {'character_voices': [{'id': '0x01', 'voice': '0x0B', 'clips': {'v1': 'c.rwav'}, 'pack': 'Ros'}]}
            clips = {'c.rwav': b'data'}
        self.assertEqual(slot_zip.voice_of({'id': 1, 'voice': 0x09}, Derived()),
                         {'source': '0x0B', 'own': True, 'clips': {'v1': b'data'}, 'pack': 'Ros'})


class StatDocTests(unittest.TestCase):
    def test_values_and_chemistry(self):
        doc, left_out = slot_zip.stat_doc(snapshot(), 0x00, 0x66, SHA1, 'Fire Mario.sluggiezip')
        self.assertEqual(left_out, [])
        self.assertEqual((doc['format'], doc['main_dol_sha1'], doc['origin']),
                         ('sluggies-stat-edits', SHA1, slot_zip.STAT_ORIGIN))
        groups = doc['characters']['0x66']
        self.assertEqual(groups['size'][_field('size').name], 1.25)
        # the own pair becomes the target's; the pair with the target stays; new IDs are left out
        self.assertEqual(groups['chemistry'], {'0x66': 2, '0x09': 0})

    def test_values_that_do_not_fit_are_left_out(self):
        snap = {'fields': {'size': {_field('size').name: '00'}, 'stats': {'nope': '00'}}, 'chemistry': {'0x09': [7, 7]}}
        doc, left_out = slot_zip.stat_doc(snap, 0x00, 0x66, SHA1, 'p')
        self.assertEqual(sorted(left_out), sorted([f'size.{_field("size").name}', 'stats.nope']))
        self.assertEqual(doc['characters']['0x66'], {})

    def test_title_names_the_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, _ = slot_zip.stat_doc(snapshot(), 0x00, 0x66, SHA1, 'Fire Mario.sluggiezip')
            path = slot_zip.write_stat_file(tmp, doc, now=0)
            self.assertTrue(os.path.basename(path).startswith('stat_edits_'))     # the Stat Editor tab prunes it
            self.assertEqual(gui_grid._edit_title({'op': gui_grid.STAT_EDITS, 'file': path}),
                             'Pending: stat values from Fire Mario.sluggiezip')
            self.assertEqual(gui_grid.stat_file_ids(path), frozenset({0x66}))


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self, path, cid=0x66, st=None, **kw):
        kw.setdefault('sound_archive', True)
        return slot_zip.stage(path, cid, st or state(cid), self.root, **kw)

    def test_every_aspect(self):
        staged = self.stage(write_package(self.root, manifest()))
        ops = [(e['op'], e.get('view') or e.get('file') if e['op'] in ('icon', 'equip') else None)
               for e in staged.edits]
        self.assertEqual(ops, [('patch', None), ('equip', 2), ('anim', None), ('icon', 'front'), ('icon', 'side'),
                               ('rename', None), ('stats', None), ('stat_edits', None), ('char_voice', None)])
        patch = staged.edits[0]
        self.assertEqual((patch['file'], patch['low'], patch['gear']),
                         (os.path.join(staged.folder, *HIGH.split('/')), os.path.join(staged.folder, *LOW.split('/')),
                          False))
        self.assertTrue(os.path.isfile(patch['file']))
        self.assertEqual(staged.edits[5], {'op': 'rename', 'id': '0x66', 'text': 'Fire Mario'})
        self.assertEqual(staged.edits[6], {'op': 'stats', 'id': '0x66', 'source': '0x00'})
        self.assertEqual(staged.edits[8], {'op': 'char_voice', 'id': '0x66', 'source': '0x00'})
        self.assertEqual(staged.skipped, [])
        # every edit is one the planner reads (the staging check gets them as they are)
        self.assertEqual(len(slot_plan.parse_edits({'edits': staged.edits})), len(staged.edits))

    def test_missing_files_skip_their_aspect(self):
        path = write_package(self.root, manifest(), leave_out=(HIGH, BAT, SIDE, slot_zip.STATS_FILE))
        staged = self.stage(path)
        ops = [e['op'] for e in staged.edits]
        self.assertNotIn('patch', ops)
        self.assertNotIn('equip', ops)
        self.assertNotIn('stat_edits', ops)
        self.assertNotIn('stats', ops)
        self.assertEqual([e['view'] for e in staged.edits if e['op'] == 'icon'], ['front'])
        self.assertEqual(staged.skipped, ['Models: the High model is not in the package',
                                          'Equipment: file 2 not in the package',
                                          'Side portrait: not in the package', 'Stats: not in the package'])

    def test_the_slot_decides(self):
        st = state(0x66, voice=0x00)
        staged = self.stage(write_package(self.root, manifest(voice={'source': '0x00', 'own': False, 'pack': None})),
                            st=st, name_problem=lambda text: 'too wide for the name plate',
                            can_portrait=lambda cid: False)
        ops = [e['op'] for e in staged.edits]
        self.assertNotIn('rename', ops)
        self.assertNotIn('icon', ops)
        self.assertNotIn('char_voice', ops)          # it speaks with that voice already
        self.assertIn("Name: 'Fire Mario': too wide for the name plate", staged.skipped)
        self.assertIn('Portraits: this slot shows the Mii icon (no portraits of its own)', staged.skipped)

    def test_no_sound_archive_or_stat_tables(self):
        st = state()
        del st['sizes']
        staged = self.stage(write_package(self.root, manifest()), st=st, sound_archive=False)
        ops = [e['op'] for e in staged.edits]
        self.assertNotIn('char_voice', ops)
        self.assertNotIn('stat_edits', ops)
        self.assertIn('stats', ops)                 # the stats source needs neither
        self.assertTrue(any(s.startswith('Voice: needs the sound archive') for s in staged.skipped))
        self.assertTrue(any(s.startswith('Stat values: the stat tables') for s in staged.skipped))

    def test_voice_pack_is_installed_once(self):
        voice = slot_zip.voice_pack_bytes({'source': '0x0B', 'clips': {'v1': rwav()}, 'pack': 'Rosalina'}, 'Fire')
        doc = manifest(voice={'source': '0x0B', 'own': True, 'pack': slot_zip.VOICE_FILE})
        path = write_package(self.root, doc, extra={slot_zip.VOICE_FILE: voice})
        first = self.stage(path)
        self.assertEqual(first.edits[-1], {'op': 'char_voice', 'id': '0x66', 'source': '0x0B', 'pack': 'Rosalina'})
        self.assertEqual(first.created_pack, 'Rosalina')
        pack = sound_packs.find_pack(self.root, 'Rosalina')
        self.assertEqual(sorted(pack.clips), ['v1'])
        second = self.stage(path)                   # the same clips: the pack is reused
        self.assertIsNone(second.created_pack)
        self.assertEqual(second.edits[-1]['pack'], 'Rosalina')
        sound_packs.set_clip(pack, 'v3', rwav())    # another pack of that name: the package's gets a new name
        third = self.stage(path)
        self.assertEqual(third.created_pack, 'Rosalina 2')
        slot_zip.remove_pack(self.root, third.created_pack)
        self.assertIsNone(sound_packs.find_pack(self.root, 'Rosalina 2'))

    def test_refusals(self):
        bad = os.path.join(self.root, 'bad.sluggiezip')
        with zipfile.ZipFile(bad, 'w') as zf:
            zf.writestr('readme.txt', 'x')
        with self.assertRaisesRegex(slot_zip.PackageError, 'manifest.json missing'):
            self.stage(bad)
        with self.assertRaisesRegex(slot_zip.PackageError, 'version 9'):
            self.stage(write_package(self.root, manifest(version=9)))
        with zipfile.ZipFile(bad, 'w') as zf:
            zf.writestr(slot_zip.MANIFEST, json.dumps(manifest()))
            zf.writestr('../escape.txt', 'x')
        with self.assertRaisesRegex(slot_zip.PackageError, 'unexpected member'):
            self.stage(bad)
        self.assertFalse(os.path.exists(os.path.join(self.root, '_gui', 'slot', 'escape.txt')))

    def test_prune_keeps_what_pending_edits_use(self):
        path = write_package(self.root, manifest())
        kept, dropped = self.stage(path), self.stage(path)
        self.assertNotEqual(kept.folder, dropped.folder)
        slot_zip.prune(os.path.join(self.root, slot_zip.STAGED_REL), {kept.edits[0]['file']})
        self.assertTrue(os.path.isdir(kept.folder))
        self.assertFalse(os.path.exists(dropped.folder))


class GuiTests(unittest.TestCase):
    STATE = {'characters': [{'id': 0x66, 'name': {'en': 'Fire: Mario?'}}], 'squares': [], 'cells': []}

    def test_package_name_and_command(self):
        self.assertEqual(gui_grid.package_name(self.STATE, 0x66), 'Fire Mario.sluggiezip')
        self.assertEqual(gui_grid.package_export_command(0x66, 'C:/x.sluggiezip'),
                         ('--export-sluggiezip', '0x66', 'C:/x.sluggiezip'))
        self.assertEqual((gui_grid.PACKAGE_EXTENSION, gui_grid.PACKAGE_EXPORT_REL, gui_grid.PACKAGE_STAT_ORIGIN),
                         (slot_zip.EXTENSION, os.path.join('_gui', slot_zip.RESULT_FILE), slot_zip.STAT_ORIGIN))

    def test_package_dialog(self):
        section = {'target': '0x66', 'action': 'rename', 'edit': {'op': 'rename', 'id': '0x66', 'text': 'Fire Mario'},
                   'notes': ["renamed to 'Fire Mario'"], 'warnings': [], 'effects': {}, 'rebuild': True}
        plan = {'refused': [], 'edits': [dict(section, checked=True), section], 'notes': [],
                'skipped': [{'target': '0x66', 'action': 'stats', 'notes': ['nothing to change: same stats']}]}
        dialog = gui_grid.package_dialog(self.STATE, 0x66, 'C:/p/Fire.sluggiezip', plan, 0, '', ['Voice: not in it'])
        text = [line for line, _kind in dialog.lines]
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Load Fire.sluggiezip into Fire: Mario? (0x66)?')
        self.assertEqual(text.count("Rename to 'Fire Mario'"), 1)          # the pending (checked) one is not repeated
        self.assertIn('Unchanged: nothing to change: same stats', text)
        self.assertIn('Skipped: Voice: not in it', text)
        refused = dict(plan, refused=[{'target': '0x66', 'edit': {'op': 'patch'}, 'error': 'skeletons differ'}])
        dialog = gui_grid.package_dialog(self.STATE, 0x66, 'Fire.sluggiezip', refused, 1, '', [])
        self.assertFalse(dialog.can_apply)
        self.assertIn('Refused: skeletons differ', [line for line, _kind in dialog.lines])
        failed = gui_grid.package_dialog(self.STATE, 0x66, 'Fire.sluggiezip', plan, 1, '', [])
        self.assertFalse(failed.can_apply)

    def test_staged_package_files(self):
        pending = gui_grid.PendingEdits()
        pending.edits = [{'op': 'patch', 'id': '0x66', 'file': 'a', 'low': 'b'}, {'op': 'anim', 'id': '0x66', 'anm': 'c'},
                         {'op': 'equip', 'id': '0x66', 'file': 2, 'sluggie': 'd'}, {'op': 'rename', 'id': '0x66'}]
        self.assertEqual(gui_grid.staged_package_files(pending), {'a', 'b', 'c', 'd'})


if __name__ == '__main__':
    unittest.main()
