"""Character files of the Sluggers Characters patcher (``Roster/slgmodel.py``) and their import
(``slot_plan.plan_slgmodel``, the ``slgmodel`` edit, ``slot_cli.write_voice_pack``).

The deltas and zips are built here from synthetic bytes (no game data); the plans run on the hand-made read
state and derived config of ``test_slot_plan`` with a fake ``Env``.
"""

import hashlib
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

from PIL import Image

TESTS_DIR = pathlib.Path(__file__).resolve().parent
for _p in (TESTS_DIR.parent, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from SluggiesTools import gui_grid  # noqa: E402
from SluggiesTools.Roster import slgmodel, slot_cli, slot_plan  # noqa: E402
from SluggiesTools.Sound import sound_packs  # noqa: E402
from SluggiesTools.tests.test_slot_plan import NAMES_TEXT, STATE_FILE, char, make_config  # noqa: E402
from SluggiesTools.tests import test_slot_plan  # noqa: E402
import synthetic_sound as synth  # noqa: E402


def make_state(voice_set=None):
    """``test_slot_plan``'s state with the squares' voices (``head_index`` / ``voice``) and Daisy's square."""
    st = test_slot_plan.make_state(voice_set)
    for sq, (index, voice) in zip(st['squares'], ((9, 0x09), (13, 0x0D), (None, 0x04), (6, 0x47))):
        sq['voice'] = voice
        if index is not None:
            sq['head_index'] = index
    st['squares'].append({'kind': 'stock', 'head': 0x05, 'head_index': 5, 'members': [0x05], 'voice': 0x05})
    st['characters'].append(char(0x05, 4, 23, name='Daisy'))
    return st

COPY_DIR = os.path.join('/out/_gui/slot', slot_plan.COPY_DIR)
SOURCES = [bytes(range(256)) * 8, b'DAT-' * 300 + bytes(range(100, 200)) * 4, b'SOUND' * 50]
HIGH = b'HIGH-BLOCK' + SOURCES[1][40:140] + b'-end'
LOW = b'LOW-BLOCK' + SOURCES[0][5:75]
DAISY = 0x05


def varint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def make_delta(target, ops, length=None, sha=None):
    """A delta file: ``ops`` as ``('copy', source, offset, length)`` / ``('literal', bytes)``."""
    body = bytearray()
    for op in ops:
        if op[0] == 'copy':
            body += b'\x00' + varint(op[1]) + varint(op[2]) + varint(op[3])
        else:
            body += b'\x01' + varint(len(op[1])) + op[1]
    head = slgmodel.DELTA_MAGIC + struct.pack('>I', len(target) if length is None else length)
    head += hashlib.sha1(target).digest() if sha is None else sha
    return zlib.compress(head + bytes(body))


HIGH_OPS = [('literal', b'HIGH-BLOCK'), ('copy', 1, 40, 100), ('literal', b'-end')]
LOW_OPS = [('literal', b'LOW-BLOCK'), ('copy', 0, 5, 70)]


def png_bytes(size=(48, 51), color=(200, 30, 30, 255)):
    import io
    buf = io.BytesIO()
    Image.new('RGBA', size, color).save(buf, 'PNG')
    return buf.getvalue()


def wav_bytes(n=100):
    data = b'\0\0' * n
    fmt = struct.pack('<HHIIHH', 1, 1, 22050, 44100, 2, 16)
    body = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', 4 + len(body)) + b'WAVE' + body


def meta(**changes):
    base = {'format': 1, 'name': 'Rosalina', 'base': 'Daisy', 'author': 'Nick', 'from': 'Mario Kart Wii',
            'notes': 'a note', 'left_out': ['its own bat'], 'glb_sha1': None, 'blocks': [0, 1], 'voice': False}
    base.update(changes)
    return base


def write_file(folder, name='rosalina.slgmodel', doc=None, members=None, glb=None, voice=False, portraits=True,
               low=True):
    doc = dict(meta() if doc is None else doc)
    files = {'models/0.slgd': make_delta(HIGH, HIGH_OPS)}
    if low:
        files['models/1.slgd'] = make_delta(LOW, LOW_OPS)
    else:
        doc['blocks'] = [0]
    if portraits:
        files['side.png'] = png_bytes()
        files['front.png'] = png_bytes(color=(30, 200, 30, 255))
    if glb is not None:
        files['model.glb'] = glb
        doc.setdefault('glb_sha1', hashlib.sha1(glb).hexdigest())
    if voice:
        slots = slgmodel.VOICE_SLOTS if voice is True else voice
        for slot in slots:
            files[f'voice/{slot}.wav'] = wav_bytes()
        doc['voice'] = True
        doc['voice_slots'] = {s: 'own' if s in slots else 'base' for s in slgmodel.VOICE_SLOTS}
    files.update(members or {})
    path = os.path.join(folder, name)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(slgmodel.META, json.dumps(doc))
        for member, data in files.items():
            zf.writestr(member, data)
    return path


def write_inputs(folder):
    for rel, data in zip(slgmodel.DELTA_SOURCES, SOURCES):
        path = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
    return folder


class DeltaTests(unittest.TestCase):
    def test_copy_and_literal_rebuild(self):
        delta = make_delta(HIGH, HIGH_OPS)
        self.assertEqual(slgmodel.apply_delta(delta, SOURCES), HIGH)
        self.assertEqual(slgmodel.apply_delta(delta, lambda i: SOURCES[i]), HIGH)       # opened on first use
        self.assertEqual(slgmodel.delta_info(delta), (len(HIGH), hashlib.sha1(HIGH).hexdigest()))
        self.assertEqual(slgmodel.delta_sources(delta), [1])
        self.assertEqual(slgmodel.delta_sources(make_delta(LOW, LOW_OPS)), [0])

    def test_modded_game_is_refused(self):
        delta = make_delta(HIGH, HIGH_OPS)
        modded = [SOURCES[0], bytearray(SOURCES[1]), SOURCES[2]]
        modded[1][50] ^= 0xFF
        with self.assertRaisesRegex(slgmodel.SlgError, 'not the clean game.*dt_na.dat'):
            slgmodel.apply_delta(delta, modded)
        with self.assertRaisesRegex(slgmodel.SlgError, 'past its end'):
            slgmodel.apply_delta(delta, [SOURCES[0], SOURCES[1][:100], SOURCES[2]])

    def test_damaged_deltas(self):
        with self.assertRaisesRegex(slgmodel.SlgError, 'not a delta'):
            slgmodel.apply_delta(zlib.compress(b'NOPE' + bytes(40)), SOURCES)
        with self.assertRaisesRegex(slgmodel.SlgError, 'not a delta'):
            slgmodel.apply_delta(b'not even zlib', SOURCES)
        cut = zlib.compress(zlib.decompress(make_delta(HIGH, HIGH_OPS))[:-3])
        with self.assertRaisesRegex(slgmodel.SlgError, 'cut short'):
            slgmodel.apply_delta(cut, SOURCES)
        with self.assertRaisesRegex(slgmodel.SlgError, 'game file 7'):
            slgmodel.apply_delta(make_delta(b'x', [('copy', 7, 0, 1)]), SOURCES)
        with self.assertRaisesRegex(slgmodel.SlgError, 'bigger than any model'):
            slgmodel.apply_delta(make_delta(b'x', [('literal', b'x')], length=slgmodel.MAX_BLOCK + 1), SOURCES)

    def test_input_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_inputs(tmp)
            with slgmodel.InputSources(tmp) as sources:
                self.assertEqual(slgmodel.apply_delta(make_delta(HIGH, HIGH_OPS), sources), HIGH)
                self.assertEqual(slgmodel.apply_delta(make_delta(LOW, LOW_OPS), sources), LOW)
            os.remove(os.path.join(tmp, slgmodel.DELTA_SOURCES[2]))
            with slgmodel.InputSources(tmp) as sources:
                self.assertEqual(slgmodel.apply_delta(make_delta(HIGH, HIGH_OPS), sources), HIGH)   # not needed
                with self.assertRaisesRegex(slgmodel.SlgError, 'sound_NA/MY2.brsar is missing'):
                    slgmodel.apply_delta(make_delta(b'S', [('copy', 2, 0, 1)]), sources)


class BaseNameTests(unittest.TestCase):
    def test_table(self):
        self.assertEqual(slgmodel.base_id('Daisy'), DAISY)
        self.assertEqual(slgmodel.base_id(' red  shy guy '), 0x10)
        self.assertEqual(slgmodel.base_id('Green Koopa Troopa'), 0x0C)
        self.assertEqual(slgmodel.base_id('Unused Koopa'), 0x4C)
        self.assertIsNone(slgmodel.base_id('Red Mii (M)'))                 # not a player
        self.assertIsNone(slgmodel.base_id('Rosalina'))
        self.assertIsNone(slgmodel.base_id(None))
        self.assertEqual(slgmodel.base_name(0x0D), 'Red Toad')
        self.assertEqual(len(slgmodel.BASE_NAMES), 0x65)


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.inputs = write_inputs(os.path.join(self.tmp, 'in'))

    def test_full_file(self):
        path = write_file(self.tmp, glb=b'glTF-bytes', voice=True)
        m = slgmodel.read(path, self.inputs)
        self.assertEqual((m.name, m.base, m.base_id, m.author, m.source, m.notes, m.left_out),
                         ('Rosalina', 'Daisy', DAISY, 'Nick', 'Mario Kart Wii', 'a note', ['its own bat']))
        self.assertEqual((m.high, m.low, m.high_only), (HIGH, LOW, False))
        self.assertEqual({v: img.size for v, img in m.portraits.items()}, {'side': (48, 51), 'front': (48, 51)})
        self.assertEqual(set(m.voice), set(slgmodel.VOICE_SLOTS))
        self.assertEqual(m.voice_slots['v1'], 'own')
        self.assertTrue(m.has_glb)
        self.assertFalse(m.glb_edited)
        self.assertEqual(m.file_name, 'rosalina.slgmodel')

    def test_high_only_and_partial_voice(self):
        path = write_file(self.tmp, low=False, voice=['v1', 'v13'], portraits=False)
        m = slgmodel.read(path, self.inputs)
        self.assertTrue(m.high_only)
        self.assertIsNone(m.low)
        self.assertEqual(sorted(m.voice), ['v1', 'v13'])
        self.assertEqual((m.voice_slots['v1'], m.voice_slots['v3']), ('own', 'base'))
        self.assertEqual(m.portraits, {})

    def test_edited_glb_is_flagged_not_refused(self):
        path = write_file(self.tmp, doc=meta(glb_sha1='0' * 40), glb=b'edited')
        m = slgmodel.read(path, self.inputs)
        self.assertTrue(m.has_glb)
        self.assertTrue(m.glb_edited)
        self.assertEqual((m.high, m.low), (HIGH, LOW))              # the blocks as exported

    def test_meta_only_read_needs_no_game(self):
        path = write_file(self.tmp)
        m = slgmodel.read(path, os.path.join(self.tmp, 'nowhere'), blocks=False)
        self.assertEqual((m.name, m.blocks), ('Rosalina', {}))

    def test_refusals(self):
        cases = [
            (dict(doc=meta(format=2)), 'format 2'),
            (dict(doc=meta(base='Rosalina')), 'not a stock player'),
            (dict(doc=meta(base='Red Mii (M)')), 'not a stock player'),
            (dict(doc=meta(name='  ')), 'no name'),
            (dict(doc=meta(blocks=[0, 2])), 'model files'),
            (dict(doc=meta(blocks=[])), 'no model files'),
            (dict(members={'extra.txt': b'x'}), 'no place for'),
            (dict(members={'models/1.slgd': make_delta(LOW, LOW_OPS, sha=bytes(20))}), 'not the clean game'),
            (dict(members={'side.png': b'not a png'}), 'side portrait'),
            (dict(members={'voice/v1.wav': b'not a wav'}), 'not a WAV'),
        ]
        for n, (kwargs, text) in enumerate(cases):
            with self.subTest(text=text):
                path = write_file(self.tmp, name=f'c{n}.slgmodel', **kwargs)
                with self.assertRaisesRegex(slgmodel.SlgError, text):
                    slgmodel.read(path, self.inputs)
        with zipfile.ZipFile(os.path.join(self.tmp, 'nometa.slgmodel'), 'w') as zf:
            zf.writestr('side.png', png_bytes())
        with self.assertRaisesRegex(slgmodel.SlgError, 'no character.json'):
            slgmodel.read(os.path.join(self.tmp, 'nometa.slgmodel'), self.inputs)
        with open(os.path.join(self.tmp, 'text.slgmodel'), 'wb') as f:
            f.write(b'hello')
        with self.assertRaisesRegex(slgmodel.SlgError, 'not a character file'):
            slgmodel.read(os.path.join(self.tmp, 'text.slgmodel'), self.inputs)
        with mock.patch.object(slgmodel, 'MAX_FILE', 100):
            with self.assertRaisesRegex(slgmodel.SlgError, 'too big'):
                slgmodel.read(write_file(self.tmp, name='big.slgmodel'), self.inputs)
        with self.assertRaisesRegex(slgmodel.SlgError, 'missing in 1_Input'):
            slgmodel.read(write_file(self.tmp, name='noin.slgmodel'), os.path.join(self.tmp, 'nowhere'))


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------

def model(**changes):
    kw = dict(path='/files/rosalina.slgmodel', name='Rosalina', base='Daisy', base_id=DAISY, author='Nick',
              source='', notes='', left_out=['its own bat'], blocks={0: HIGH, 1: LOW},
              portraits={'side': Image.new('RGBA', (48, 51), (1, 2, 3, 255)),
                         'front': Image.new('RGBA', (48, 51), (4, 5, 6, 255))},
              voice={}, voice_slots={})
    kw.update(changes)
    return slgmodel.SlgModel(**kw)


class ImportEnv(slot_plan.Env):
    def __init__(self, slot_errors=(), archive=True, stock_art=True):
        self.slot_errors, self.archive, self.stock_art = list(slot_errors), archive, stock_art
        self.slot_calls, self.encoded, self.high_as_low = [], [], []

    def slot_problems(self, directory, high, low, high_as_low=False):
        self.slot_calls.append((directory, high, low))
        self.high_as_low.append(high_as_low)
        return list(self.slot_errors), ['a warning']

    def stock_portrait(self, cid, view):
        return Image.new('RGBA', (48, 51), (cid, 0, 0, 255)) if self.stock_art else None

    def sound_archive(self):
        return self.archive

    def encode_wav(self, data, label):
        self.encoded.append(label)
        return b'RWAV' + hashlib.sha1(data + label.encode()).digest(), ['resampled']

    def free_pack_name(self, name):
        return name + ' 2'


def plan(cid, m=None, env=None, st=None, config=None):
    return slot_plan.plan_slgmodel(st or make_state(), config or make_config(), cid, m or model(), env or ImportEnv(),
                                   STATE_FILE, NAMES_TEXT)


def entry(config, cid):
    return next(e for e in config['ids'] if int(e['id'], 16) == cid)


class NewIdPlanTests(unittest.TestCase):
    def test_open_slot_takes_the_whole_character(self):
        env = ImportEnv()
        p = plan(0x66, model(voice={s: wav_bytes() for s in slgmodel.VOICE_SLOTS}), env)
        e = entry(p.config, 0x66)
        self.assertEqual(e['model'], {'from': '0x05'})
        self.assertEqual(e['stats'], '0x05')
        self.assertEqual(e['name'], {'en': 'Rosalina', 'fr': 'Rosalina', 'sp': 'Rosalina'})
        self.assertEqual(e['icon'], {'side': 'slg_66_side.png', 'front': 'slg_66_front.png', 'fit': 'strict',
                                     'like': '0x04'})
        self.assertEqual(p.config['grid']['squares'][0], {'members': ['0x66', '0x67'], 'voice': '0x05'})
        self.assertEqual(set(p.portraits), {'slg_66_side.png', 'slg_66_front.png'})
        voice = p.config['character_voices'][0]
        self.assertEqual((voice['id'], voice['voice'], voice['pack'], len(voice['clips'])),
                         ('0x66', '0x05', 'Rosalina 2', 12))
        self.assertEqual(set(voice['clips'].values()), set(p.clips))
        self.assertEqual((p.voice_pack['name'], p.voice_pack['voice'], p.voice_pack['origin'],
                          len(p.voice_pack['clips'])), ('Rosalina 2', DAISY, 'rosalina.slgmodel', 12))
        self.assertEqual(p.voice_pack['clips']['v1'], {'rwav': 'slg_66_v1.rwav', 'wav': 'slg_66_v1.wav'})
        self.assertEqual(len(env.encoded), 12)
        self.assertEqual(env.slot_calls, [(DAISY + 0x12, HIGH, LOW)])
        self.assertEqual(env.high_as_low, [True])                     # a character file may ship two High models
        hp, lp = os.path.join(COPY_DIR, 'slg_66_hp.bin'), os.path.join(COPY_DIR, 'slg_66_l.bin')
        spec = os.path.join(COPY_DIR, 'slg_66_pack.json')
        self.assertEqual(p.commands, [('--roster', '--state', STATE_FILE), ('--write-slot-blocks', '0x66', hp, lp, '--high-as-low'),
                                      ('--write-voice-pack', spec), ('--roster-state',)])
        self.assertEqual((p.copy_files['slg_66_hp.bin'], p.copy_files['slg_66_l.bin']), (HIGH, LOW))
        self.assertIs(p.copy_files['slg_66_pack.json'], p.voice_pack)      # the chain step reads the pack from there
        self.assertEqual(p.copy_files['slg_66_v1.rwav'], p.clips[voice['clips']['v1']])
        self.assertEqual(p.copy_files['slg_66_v1.wav'][:4], b'RIFF')
        self.assertEqual(p.effects['name'], 'Rosalina')
        self.assertEqual(p.effects['stats'], 'Daisy (0x05)')
        self.assertIn('pack Rosalina 2', p.effects['char_voice'])
        self.assertEqual(set(p.effects['portraits']), {'front', 'side'})
        self.assertIn('a warning', p.warnings)
        self.assertIn('its own bat', ' '.join(p.notes))
        doc = p.to_json()
        self.assertEqual((doc['action'], doc['source'], doc['slgmodel']['name'], doc['slgmodel']['high_only']),
                         ('slgmodel', '0x05', 'Rosalina', False))
        self.assertEqual(doc['slgmodel']['sizes'], {'high': len(HIGH), 'low': len(LOW)})

    def test_high_only_writes_the_high_block_twice(self):
        p = plan(0x66, model(blocks={0: HIGH}))
        hp = os.path.join(COPY_DIR, 'slg_66_hp.bin')
        self.assertIn(('--write-slot-blocks', '0x66', hp, hp, '--high-as-low'), p.commands)
        self.assertNotIn('slg_66_l.bin', p.copy_files)
        self.assertTrue(any('used as the Low model too' in w for w in p.warnings))
        self.assertTrue(p.to_json()['slgmodel']['high_only'])

    def test_edited_glb_warns_and_writes_the_exported_blocks(self):
        p = plan(0x66, model(has_glb=True, glb_edited=True))
        self.assertTrue(any('model.glb was edited' in w and 'without those edits' in w for w in p.warnings))
        self.assertEqual((p.copy_files['slg_66_hp.bin'], p.copy_files['slg_66_l.bin']), (HIGH, LOW))
        self.assertTrue(p.to_json()['slgmodel']['glb_edited'])
        self.assertFalse(plan(0x66).to_json()['slgmodel']['glb_edited'])

    def test_without_the_archive_the_clips_are_left_out(self):
        env = ImportEnv(archive=False)
        p = plan(0x66, model(voice={'v1': wav_bytes()}), env)
        self.assertNotIn('character_voices', p.config)
        self.assertIsNone(p.voice_pack)
        self.assertNotIn('--write-voice-pack', [c[0] for c in p.commands])
        self.assertEqual(env.encoded, [])
        self.assertTrue(any('1 voice clips' in w and 'sound_NA' in w for w in p.warnings))

    def test_no_clips_on_a_square_of_another_family_takes_the_base_voice(self):
        p = plan(0x68)                                   # Bowser's square, base Daisy
        self.assertEqual(p.config['character_voices'], [{'id': '0x68', 'voice': '0x05'}])
        self.assertEqual(p.effects['char_voice'], 'Daisy (0x05)')
        self.assertNotIn('stats', p.effects)             # a stock square keeps its stats
        self.assertEqual(p.config['grid']['squares'][0], ['0x66', '0x67'])
        p = plan(0x68, env=ImportEnv(archive=False))
        self.assertNotIn('character_voices', p.config)
        self.assertTrue(any('voice of its own' in w for w in p.warnings))

    def test_no_clips_on_the_base_family_square_needs_no_own_voice(self):
        config = make_config()
        config['character_voices'] = [{'id': '0x66', 'voice': '0x09'}]      # an earlier own voice is reset
        p = plan(0x66, config=config)                    # the new square takes Daisy's voice
        self.assertNotIn('character_voices', p.config)
        self.assertNotIn('char_voice', p.effects)

    def test_long_name_is_not_taken(self):
        p = plan(0x66, model(name='A' * 60))
        self.assertEqual(entry(p.config, 0x66)['name'], make_config()['ids'][0]['name'])
        self.assertNotIn('name', p.effects)
        self.assertTrue(any('does not fit the name plate' in w for w in p.warnings))

    def test_missing_portrait_takes_the_base_stock_art(self):
        p = plan(0x66, model(portraits={'front': Image.new('RGBA', (48, 51))}))
        self.assertEqual(entry(p.config, 0x66)['icon']['side'], 'slg_66_side.png')
        self.assertEqual(p.portraits['slg_66_side.png'].getpixel((0, 0)), (DAISY, 0, 0, 255))
        self.assertTrue(any('no side portrait' in w for w in p.warnings))
        p = plan(0x66, model(portraits={}), ImportEnv(stock_art=False))
        self.assertEqual(entry(p.config, 0x66)['icon'], make_config()['ids'][0]['icon'])
        self.assertNotIn('portraits', p.effects)
        self.assertTrue(any('keeps its own' in n for n in p.notes))

    def test_other_size_portrait_is_fitted(self):
        p = plan(0x66, model(portraits={'front': Image.new('RGBA', (96, 102), (9, 9, 9, 255)),
                                        'side': Image.new('RGBA', (48, 51), (9, 9, 9, 255))}))
        self.assertEqual(p.portraits['slg_66_front.png'].size, (48, 51))
        self.assertTrue(any('not 48x51' in w for w in p.warnings))

    def test_own_directory_of_the_base_is_kept_other_copied_afresh(self):
        config = make_config()
        config['ids'][1]['model'] = {'from': '0x05', 'routes': [[1, 2]]}
        st = make_state()
        next(c for c in st['characters'] if c['id'] == 0x67)['model_source'] = DAISY
        p = plan(0x67, st=st, config=config)
        self.assertEqual(entry(p.config, 0x67)['model'], {'from': '0x05', 'routes': [[1, 2]]})
        self.assertIn('kept', p.effects['model'])
        p = plan(0x67)                                   # holds Bowser's files now
        self.assertEqual(entry(p.config, 0x67)['model'], {'from': '0x05'})
        self.assertTrue(any('dropped' in n for n in p.notes))

    def test_blocks_that_do_not_fit_are_refused(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'does not fit a slot built on.*92 bones'):
            plan(0x66, env=ImportEnv(slot_errors=['92 bones vs 89']))


class StockSlotPlanTests(unittest.TestCase):
    def test_models_only(self):
        env = ImportEnv()
        p = plan(0x0D, env=env)
        self.assertIsNone(p.config)
        self.assertEqual(env.slot_calls, [(31, HIGH, LOW)])
        self.assertEqual(env.high_as_low, [True])
        self.assertEqual(p.commands, [('--write-slot-blocks', '0x0D', os.path.join(COPY_DIR, 'slg_0D_hp.bin'),
                                       os.path.join(COPY_DIR, 'slg_0D_l.bin'), '--high-as-low'), ('--roster-state',)])
        self.assertTrue(any('models only' in n for n in p.notes))
        self.assertEqual(set(p.effects), {'model'})
        self.assertIsNone(p.voice_pack)

    def test_other_skeleton_is_refused(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'same skeleton'):
            plan(0x0D, env=ImportEnv(slot_errors=['the skeletons do not match']))


class EditTests(unittest.TestCase):
    def test_parse_and_json(self):
        edits = slot_plan.parse_edits({'edits': [{'op': 'slgmodel', 'id': '0x66', 'file': '/f/x.slgmodel'}]})
        self.assertEqual((edits[0].op, edits[0].cid, edits[0].file), ('slgmodel', 0x66, '/f/x.slgmodel'))
        self.assertEqual(edits[0].to_json(), {'op': 'slgmodel', 'id': '0x66', 'file': '/f/x.slgmodel'})
        with self.assertRaisesRegex(slot_plan.PlanError, 'needs a "file"'):
            slot_plan.parse_edits([{'op': 'slgmodel', 'id': '0x66'}])
        self.assertIn(slot_plan.SLGMODEL, slot_plan.MODEL_OPS)
        self.assertEqual(gui_grid.MODEL_OPS, slot_plan.MODEL_OPS)

    def test_merge_drops_earlier_edits_and_keeps_later_ones(self):
        edits = [slot_plan.Edit('rename', 0x66, text='Old', index=1),
                 slot_plan.Edit('slgmodel', 0x66, '/f/x.slgmodel', index=2),
                 slot_plan.Edit('rename', 0x66, text='New', index=3)]
        merged, notes, refused = slot_plan.merge_edits(edits)
        self.assertEqual([(e.op, e.text) for e in merged], [('slgmodel', None), ('rename', 'New')])
        self.assertTrue(any('character file import' in n for n in notes))
        self.assertEqual(refused, [])

    def test_batch(self):
        class BatchEnv(ImportEnv):
            def read_slgmodel(self, path):
                if path.endswith('bad.slgmodel'):
                    raise slot_plan.PlanError('bad.slgmodel is damaged')
                return model(path=path)
        edits = [slot_plan.Edit('slgmodel', 0x66, '/f/rosalina.slgmodel', index=1),
                 slot_plan.Edit('rename', 0x66, text='Rosie', index=2)]
        batch = slot_plan.plan_batch(make_state(), make_config(), edits, BatchEnv(), STATE_FILE, NAMES_TEXT)
        self.assertTrue(batch.ok)
        self.assertEqual([c[0] for c in batch.commands], ['--roster', '--write-slot-blocks', '--roster-state'])
        self.assertEqual(entry(batch.config, 0x66)['name']['en'], 'Rosie')
        self.assertIn('slg_66_hp.bin', batch.copy_files)

        class VoicedEnv(BatchEnv):
            def read_slgmodel(self, path):
                return model(path=path, voice={'v1': wav_bytes()})
        batch = slot_plan.plan_batch(make_state(), make_config(), edits, VoicedEnv(), STATE_FILE, NAMES_TEXT)
        self.assertEqual([c[0] for c in batch.commands],
                         ['--roster', '--write-slot-blocks', '--write-voice-pack', '--roster-state'])
        self.assertIn('slg_66_pack.json', batch.copy_files)
        batch = slot_plan.plan_batch(make_state(), make_config(),
                                     [slot_plan.Edit('slgmodel', 0x66, '/f/bad.slgmodel', index=1)],
                                     BatchEnv(), STATE_FILE, NAMES_TEXT)
        self.assertEqual([r[1] for r in batch.refused], ['bad.slgmodel is damaged'])
        self.assertEqual(batch.commands, [])

    def test_gui_texts(self):
        self.assertEqual(gui_grid._edit_title({'op': 'slgmodel', 'id': '0x66', 'file': '/f/rosalina.slgmodel'}),
                         'Pending: import the character file rosalina.slgmodel into this slot')
        p = plan(0x66)
        section = dict(p.to_json(), edit={'op': 'slgmodel', 'id': '0x66', 'file': p.slgmodel['file']})
        lines = gui_grid._section_lines(make_state(), section, {})
        text = ' '.join(line for line, _color in lines)
        self.assertIn('Character file: rosalina.slgmodel: Rosalina, built on', text)
        self.assertIn('Models: High', text)
        self.assertIn('Name: Rosalina', text)
        self.assertNotIn('edits are not included', text)
        edited = plan(0x66, model(has_glb=True, glb_edited=True))
        section_e = dict(edited.to_json(), edit=section['edit'])
        lines_e = gui_grid._section_lines(make_state(), section_e, {})
        self.assertIn(('Models: its model.glb was edited after export; those edits are not included', gui_grid.WARN),
                      lines_e)
        dialog = gui_grid.slot_dialog(make_state(), 0x66, False, {'refused': [], 'edits': [section], 'skipped': [],
                                                                   'notes': [], 'warnings': [], 'merged': []},
                                      0, '', None, kind='slgmodel')
        self.assertEqual(dialog.title, 'Import a character file into Empty slot (0x66)?')
        self.assertTrue(dialog.can_apply)


class VoicePackWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _archive, rwav_of = synth.build_voice_archive()
        cls.rwav = rwav_of[(3, 0)]

    def test_packs_are_created_with_a_free_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            sound_packs.create_pack(tmp, 'Rosalina', DAISY)
            copies = os.path.join(tmp, '_gui', 'slot', 'copies')
            spec = {'name': 'Rosalina', 'voice': DAISY, 'origin': 'rosalina.slgmodel',
                    'clips': {'v1': {'rwav': 'slg_66_v1.rwav', 'wav': 'slg_66_v1.wav'},
                              'v13': {'rwav': 'slg_66_v13.rwav', 'wav': None}}}
            slot_cli.write_copy_files({'slg_66_pack.json': spec, 'slg_66_v1.rwav': self.rwav,
                                       'slg_66_v1.wav': wav_bytes(), 'slg_66_v13.rwav': self.rwav}, copies)
            name = slot_cli.write_voice_pack(os.path.join(copies, 'slg_66_pack.json'), tmp)
            self.assertEqual(name, 'Rosalina 2')
            packs, problems = sound_packs.list_packs(tmp)
            self.assertEqual(problems, [])
            self.assertEqual([(p.name, p.voice, sorted(p.clips)) for p in packs],
                             [('Rosalina', DAISY, []), ('Rosalina 2', DAISY, ['v1', 'v13'])])
            self.assertEqual(packs[1].rwav_bytes('v1'), self.rwav)
            self.assertTrue(os.path.isfile(packs[1].wav_path('v1')))
            self.assertEqual(packs[1].clips['v1'].origin, 'rosalina.slgmodel')
            os.remove(os.path.join(copies, 'slg_66_v13.rwav'))
            with self.assertRaisesRegex(slot_plan.PlanError, 'cannot be read'):
                slot_cli.write_voice_pack(os.path.join(copies, 'slg_66_pack.json'), tmp)
            self.assertEqual(len(sound_packs.list_packs(tmp)[0]), 2)     # nothing half-written


if __name__ == '__main__':
    unittest.main()
