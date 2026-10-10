"""Voice packs (``Sound/sound_packs.py``): custom pack folders, their one-file form, and the stock packs read from
a synthetic DOL and archive."""

import json
import os
import pathlib
import sys
import tempfile
import unittest
import zipfile

TESTS_DIR = pathlib.Path(__file__).resolve().parent
for _p in (TESTS_DIR.parent, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from SluggiesTools.Sound import brsar, clips, sound_packs  # noqa: E402
from SluggiesTools.tests.test_roster_grid import image_for_grid  # noqa: E402
from SluggiesTools.tests.test_roster_voice_stats_reassign import fill_voice_tables  # noqa: E402
import synthetic_sound as synth  # noqa: E402


class NameTests(unittest.TestCase):
    def test_check_name(self):
        self.assertIsNone(sound_packs.check_name('Rosalina'))
        self.assertIsNone(sound_packs.check_name('Larry Koopa 2'))
        for bad in ('', ' x', 'x.', 'a/b', 'a:b', 'CON', 'com1', 'x' * 49, None):
            with self.subTest(bad=bad):
                self.assertIsNotNone(sound_packs.check_name(bad))

    def test_moments(self):
        self.assertEqual(len(sound_packs.MOMENTS), 12)
        self.assertEqual(sound_packs.SLOT_OF['v13'], 10)
        self.assertEqual(sound_packs.MOMENT_KEYS[0], 'v1')
        self.assertIn('name call', sound_packs.moment_label('v13'))


class CustomPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive_bytes, cls.rwav_of = synth.build_voice_archive()
        cls.a, cls.b = cls.rwav_of[(3, 0)], cls.rwav_of[(4, 10)]

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())

    def test_create_fill_and_list(self):
        self.assertEqual(sound_packs.list_packs(self.tmp), ([], []))
        pack = sound_packs.create_pack(self.tmp, 'Rosa', 0x09)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, 'Sound_Packs', 'Rosa', 'pack.json')))
        clip = sound_packs.set_clip(pack, 'v1', self.a, b'RIFF-wav', origin='C:/x/cheer.wav')
        self.assertEqual((clip.rwav, clip.wav, clip.origin), ('v1.rwav', 'v1.wav', 'C:/x/cheer.wav'))
        self.assertEqual(clip.seconds, round(14 / 32000, 3))
        sound_packs.set_clip(pack, 'v13', self.b)
        self.assertEqual(pack.rwav_bytes('v1'), self.a)
        self.assertIsNone(pack.wav_path('v13'))
        packs, problems = sound_packs.list_packs(self.tmp)
        self.assertEqual(([p.name for p in packs], problems), (['Rosa'], []))
        again = packs[0]
        self.assertEqual((again.voice, again.count, sorted(again.clips)), (0x09, 2, ['v1', 'v13']))
        self.assertEqual(again.rwav_bytes('v13'), self.b)
        self.assertEqual(sound_packs.find_pack(self.tmp, 'Rosa').name, 'Rosa')
        self.assertIsNone(sound_packs.find_pack(self.tmp, 'Nope'))
        self.assertIsNone(sound_packs.find_pack(self.tmp, '../Rosa'))
        self.assertEqual(sound_packs.pack_wave_bytes(again), len(self.a) + len(self.b))
        self.assertTrue(sound_packs.remove_clip(again, 'v1'))
        self.assertFalse(sound_packs.remove_clip(again, 'v1'))
        self.assertFalse(os.path.exists(os.path.join(again.folder, 'v1.rwav')))
        sound_packs.set_voice(again, None)
        self.assertIsNone(sound_packs.load_pack(again.folder).voice)
        sound_packs.rename_pack(self.tmp, again, 'Rosalina')
        self.assertEqual([p.name for p in sound_packs.list_packs(self.tmp)[0]], ['Rosalina'])
        sound_packs.delete_pack(again)
        self.assertEqual(sound_packs.list_packs(self.tmp), ([], []))

    def test_refusals(self):
        pack = sound_packs.create_pack(self.tmp, 'Rosa')
        with self.assertRaises(sound_packs.PackError):
            sound_packs.create_pack(self.tmp, 'Rosa')
        with self.assertRaises(sound_packs.PackError):
            sound_packs.create_pack(self.tmp, 'a/b')
        with self.assertRaises(sound_packs.PackError):
            sound_packs.set_clip(pack, 'v2', self.a)
        with self.assertRaises(ValueError):                           # clips.ClipError
            sound_packs.set_clip(pack, 'v1', b'not an rwav')
        sound_packs.create_pack(self.tmp, 'Other')
        with self.assertRaises(sound_packs.PackError):
            sound_packs.rename_pack(self.tmp, pack, 'Other')
        # a damaged pack is listed as a problem, the others still load
        with open(os.path.join(pack.folder, 'pack.json'), 'w') as f:
            f.write('{"format": 1, "clips": {"v1": {"rwav": "gone.rwav"}}}')
        packs, problems = sound_packs.list_packs(self.tmp)
        self.assertEqual([p.name for p in packs], ['Other'])
        self.assertEqual(len(problems), 1)
        with open(os.path.join(pack.folder, 'pack.json'), 'w') as f:
            json.dump({'format': 1, 'clips': {'v1': {'rwav': '../x.rwav'}}}, f)
        with self.assertRaisesRegex(sound_packs.PackError, 'plain file name'):
            sound_packs.load_pack(pack.folder)

    def test_export_and_import(self):
        pack = sound_packs.create_pack(self.tmp, 'Rosa', 0x09)
        sound_packs.set_clip(pack, 'v1', self.a, b'RIFF-wav', origin='cheer.wav')
        sound_packs.set_clip(pack, 'v13', self.b)
        path = sound_packs.export_pack(pack, os.path.join(self.tmp, 'out', 'rosa'))
        self.assertTrue(path.endswith(sound_packs.PACK_EXTENSION))
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(sorted(zf.namelist()), ['pack.json', 'v1.rwav', 'v1.wav', 'v13.rwav'])
        read, files = sound_packs.read_pack_file(path)
        self.assertEqual((read.name, read.voice, sorted(read.clips)), ('Rosa', 0x09, ['v1', 'v13']))
        self.assertEqual(files['v1.rwav'], self.a)
        with self.assertRaises(sound_packs.PackError):
            sound_packs.import_pack(self.tmp, path)                    # the name is taken
        imported = sound_packs.import_pack(self.tmp, path, name='Copy')
        self.assertEqual((imported.name, imported.count, imported.rwav_bytes('v13')), ('Copy', 2, self.b))
        self.assertEqual(imported.clips['v1'].wav, 'v1.wav')
        self.assertTrue(os.path.isfile(imported.wav_path('v1')))
        other = sound_packs.create_pack(os.path.join(self.tmp, 'elsewhere'), 'Rosa')
        self.assertEqual(sound_packs.import_pack(os.path.join(self.tmp, 'elsewhere', 'z'), path).name, 'Rosa')
        bad = os.path.join(self.tmp, 'bad' + sound_packs.PACK_EXTENSION)
        with zipfile.ZipFile(bad, 'w') as zf:
            zf.writestr('pack.json', json.dumps({'format': 1, 'clips': {'v1': {'rwav': 'v1.rwav'}}}))
        with self.assertRaisesRegex(sound_packs.PackError, 'missing'):
            sound_packs.read_pack_file(bad)
        with zipfile.ZipFile(bad, 'w') as zf:
            zf.writestr('pack.json', json.dumps({'format': 1}))
            zf.writestr('sub/x.rwav', b'')
        with self.assertRaisesRegex(sound_packs.PackError, 'unexpected'):
            sound_packs.read_pack_file(bad)
        with open(bad, 'wb') as f:
            f.write(b'nope')
        with self.assertRaisesRegex(sound_packs.PackError, 'not a voice pack'):
            sound_packs.read_pack_file(bad)
        del other


class StockPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data, cls.rwav_of = synth.build_voice_archive()
        cls.archive = brsar.Brsar(data)
        cls.image = image_for_grid()
        fill_voice_tables(cls.image)

    def test_stock_packs(self):
        packs = sound_packs.stock_packs(self.image, self.archive)
        self.assertEqual(len(packs), 41)
        mario = packs[0]
        self.assertEqual((mario.species, mario.head, mario.family_file), (0, 0, 0))
        self.assertEqual([c.moment for c in mario.clips], list(sound_packs.MOMENT_KEYS))
        self.assertEqual([c.sound_id for c in mario.clips], [synth.voice_id(0, k) for k in range(12)])
        self.assertEqual(mario.clips[10].file_id, 43)                   # the name call: the select file
        self.assertEqual(mario.clips[0].name, 'sp00_v0')
        self.assertEqual(mario.prefix, 'sp00')
        self.assertEqual(mario.clips[0].seconds, round(14 / 32000, 3))
        self.assertEqual(mario.clips[0].wave_bytes, len(self.rwav_of[(0, 0)]))
        self.assertEqual(mario.family_wave_bytes, len(self.archive.file_bytes(0)[1]))
        self.assertEqual(packs[9].head, 9)

    def test_stock_clip_wav(self):
        data = sound_packs.stock_clip_wav(self.archive, synth.voice_id(2, 3))
        self.assertEqual(data[:4], b'RIFF')
        self.assertEqual(data, clips.rwav_wav_bytes(self.rwav_of[(2, 3)]))


if __name__ == '__main__':
    unittest.main()
