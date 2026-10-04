"""GUI character grid, Phase 4b: own model directories for new IDs (``Roster/model_dirs.py``).

The Phase 3 harness (``test_roster_derive``) plus two source character
directories (dir 18 = ID 0x00, dir 24 = ID 0x06) with three files each in the
input DAT. Runs go through the real reset (``reset.reset``) on the previous
run's output, as ``start.py --roster`` does, so the kept routes, the reserved
ranges and the freed copies are all exercised.
"""

import json
import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Dol import dirtable, dolfile, inventory
from SluggiesTools.Hammerspace import HammerspaceHelper as hh
from SluggiesTools.Roster import dat_hammerspace as dhs
from SluggiesTools.Roster import datfile, derive, ids, layout_file, model_dirs, reset, runner, slots, state, steps
from SluggiesTools.tests.test_roster_derive import BASE, Harness, RecordingLayout

SOURCE_RECORDS = {18: 0x806C0000, 24: 0x806C1000}    # free data in the synthetic DOL (below the stats table)
SOURCE_FILES = {18: [(0x20000, 0x40), (0x20040, 0x100), (0x20140, 0x60)],
                24: [(0x30000, 0x80), (0x30080, 0x20), (0x300A0, 0x200)]}
TWO = {'ids': [{'id': '0x66', 'template': '0x00', 'model': {'from': '0x00'}},
               {'id': '0x67', 'template': '0x00', 'model': {'from': '0x00'}},
               {'id': '0x68', 'template': '0x00'}]}


def file_bytes(directory: int, index: int) -> bytes:
    _offset, length = SOURCE_FILES[directory][index]
    return bytes((directory * 7 + index * 13 + i) % 251 + 1 for i in range(length))


class ModelDirHarness(Harness):
    def setUp(self):
        super().setUp()
        image = dolfile.DolImage(self.input_dol)
        with open(self.input_dat, 'r+b') as f:
            for directory, files in SOURCE_FILES.items():
                for index, (offset, _length) in enumerate(files):
                    f.seek(offset)
                    f.write(file_bytes(directory, index))
        table = dhs.dol_base_address(dhs.hh._DIRS_START)
        pointers = list(struct.unpack(f'>{dhs.hh._DIRS_COUNT}I', image.read(table, 4 * dhs.hh._DIRS_COUNT)))
        for directory, at in SOURCE_RECORDS.items():
            pointers[directory] = at
            image.write(at, b''.join(struct.pack('>12I', *([dhs.hh._DAT_FNAME_PTR, n, o, n] * 3))
                                     for o, n in SOURCE_FILES[directory]))
        image.write(table, struct.pack(f'>{len(pointers)}I', *pointers))
        self.input_dol = image.to_bytes()
        self.output = (self.input_dol, self.input_dat)

    def run_roster(self, config: dict, icon_dir: str | None = None):
        """``runner.run`` on the previous output: reset (keeping the config's routes), every step but the layout
        copy, the manifest. Returns (DOL bytes, DAT bytes, context, DatFile, DolImage)."""
        self.runs += 1
        prev_dol, prev_dat = self.output
        path = os.path.join(self.tmp, f'out{self.runs}.dat')
        shutil.copyfile(prev_dat, path)
        dat = datfile.DatFile(path)
        keep = model_dirs.config_routes(config)
        dol_bytes, _log = reset.reset(prev_dol, self.input_dol, dat, keep)
        image = dolfile.DolImage(dol_bytes)
        ctx = steps.RosterContext(dol=image, dat=dat, config=config,
                                  icon_dir=self.icon_dir if icon_dir is None else icon_dir,
                                  input_dol=dolfile.DolImage(self.input_dol), input_dat=datfile.DatFile(self.input_dat))
        ctx.state[model_dirs.RESERVED_KEY] = list(keep)
        ctx.state[layout_file.STATE_KEY] = RecordingLayout()
        for step in steps.all_steps():
            if step.key != 'layout_file':
                step.apply(ctx)
        runner.write_manifest(ctx)
        dat.flush()
        self.output = (image.to_bytes(), path)
        with open(path, 'rb') as f:
            return image.to_bytes(), f.read(), ctx, dat, image

    def derived(self, image, dat) -> tuple[dict, str]:
        result = derive.derive(image, dat)
        self.assertEqual(result.warnings, [])
        path = derive.write(result, os.path.join(self.tmp, f'derived{self.runs}'))
        with open(path, encoding='utf-8') as f:
            return json.load(f), derive.icon_dir_of(path)


class ModelDirTests(ModelDirHarness):
    def test_two_new_ids_on_one_template_get_two_directories(self):
        _dol, dat_bytes, ctx, _dat, image = self.run_roster(TWO)

        self.assertEqual(ctx.state[model_dirs.STATE_KEY], {0x66: (172, 0), 0x67: (173, 0)})
        pointers = dirtable.pointers(image)
        self.assertEqual(len(pointers), 174)
        self.assertTrue(dirtable.is_moved(image))
        self.assertEqual(pointers[:172], dhs.dir_pointers(dolfile.DolImage(self.input_dol)))
        seen = set()
        for directory in (172, 173):
            records = model_dirs.directory_records(image, directory)
            self.assertEqual(len(records), 3)
            for index, words in enumerate(records):
                slots_ = {dhs.slot(words, lang)[:2] for lang in dhs.LANGS}
                self.assertEqual(len(slots_), 1)
                offset, length = slots_.pop()
                self.assertGreaterEqual(offset, BASE)
                self.assertNotIn(offset, seen)
                seen.add(offset)
                self.assertEqual(dat_bytes[offset:offset + length], file_bytes(18, index))
        # dirmap: own directories for 0x66/0x67, the template's for 0x68 and stock IDs
        dirmap = image.read(ctx.state['dirmap'], 2 * ids.ROWS)
        self.assertEqual([struct.unpack_from('>H', dirmap, 2 * c)[0] for c in (0x00, 0x66, 0x67, 0x68)],
                         [18, 172, 173, 18])
        for s in inventory.group(model_dirs.REVMAP_GROUP):
            self.assertNotEqual(image.u32(s.address), s.stock)
        self.assertEqual(slots.model_dir(image, 0x66), 172)
        with self.assertRaisesRegex(slots.SlotError, 'own model directory'):
            slots.model_dir(image, 0x68)
        by_id = {c['id']: c for c in state.read_state(image, ctx.dat)['characters']}
        self.assertEqual((by_id[0x67]['model_dir'], by_id[0x67]['own_model_dir'], by_id[0x67]['model_source']),
                         (173, True, 0x00))
        self.assertFalse(by_id[0x68]['own_model_dir'])

    def test_model_source_is_not_the_template(self):
        config = {'ids': [{'id': '0x66', 'template': '0x00', 'model': {'from': '0x06'}}]}
        _dol, dat_bytes, ctx, _dat, image = self.run_roster(config)
        offset, length = dhs.slot(model_dirs.directory_records(image, 172)[2], 'en')[:2]
        self.assertEqual(dat_bytes[offset:offset + length], file_bytes(24, 2))
        handles = ctx.state['tables'][ids.HANDLE_TABLE][0]
        self.assertEqual(struct.unpack('>III', image.read(handles + 12 * 0x66, 12)),
                         ids.MODEL_REQUESTS.get(0x06, (1, 2, 4)))

    def test_round_trip_is_byte_identical_and_keeps_patched_copies(self):
        dol1, dat1, _ctx, dat, image = self.run_roster(TWO)
        # a model patched into 0x66's directory: its file 0 copy changed in place
        offset, length = dhs.slot(model_dirs.directory_records(image, 172)[0], 'en')[:2]
        patched = bytes(range(1, length + 1))
        with open(self.output[1], 'r+b') as f:
            f.seek(offset)
            f.write(patched)
        dat = datfile.DatFile(self.output[1])
        expected = bytearray(dat1)
        expected[offset:offset + length] = patched

        config, icon_dir = self.derived(image, dat)
        self.assertEqual(config['ids'][0]['model']['from'], '0x00')
        self.assertEqual(len(config['ids'][0]['model']['routes']), 3)
        dol2, dat2, _ctx2, dat_b, image_b = self.run_roster(config, icon_dir)
        self.assertEqual(dol2, dol1, 'main.dol differs after read -> rebuild')
        self.assertEqual(dat2, bytes(expected), 'dt_na.dat differs after read -> rebuild')
        config2, icon_dir2 = self.derived(image_b, dat_b)
        self.assertEqual(config2, config)
        dol3, dat3, *_ = self.run_roster(config2, icon_dir2)
        self.assertEqual((dol3, dat3), (dol1, bytes(expected)))

    def test_dropped_directory_copies_are_freed(self):
        _dol, _dat, _ctx, dat, image = self.run_roster(TWO)
        old = [dhs.slot(w, 'en')[:2] for w in model_dirs.directory_records(image, 173)]
        config, icon_dir = self.derived(image, dat)
        config['ids'] = [e for e in config['ids'] if e['id'] != '0x67']
        _dol2, dat2, ctx2, _dat2, image2 = self.run_roster(config, icon_dir)
        self.assertEqual(ctx2.state[model_dirs.STATE_KEY], {0x66: (172, 0)})
        live = {o for _c, _i, _r, w in dhs.iter_records(image2) for o, _n, _a in [dhs.slot(w, 'en')]}
        freed = [(o, n) for o, n in old if o not in live]
        self.assertEqual(len(freed), 3)
        for offset, length in freed:
            self.assertEqual(dat2[offset:offset + length], bytes(length))

    def test_config_without_model_keys_moves_nothing(self):
        _dol, _dat, ctx, _d, image = self.run_roster({'ids': [{'id': '0x66', 'template': '0x00'}]})
        self.assertFalse(dirtable.is_moved(image))
        for s in inventory.group(model_dirs.REVMAP_GROUP):
            self.assertEqual(image.u32(s.address), s.stock)
        self.assertNotIn(model_dirs.STATE_KEY, ctx.state)

    def test_routes_must_be_hammerspace_copies(self):
        config = {'ids': [{'id': '0x66', 'template': '0x00',
                           'model': {'from': '0x00', 'routes': [[0x20000, 0x40], [0x20040, 0x100], [0x20140, 0x60]]}}]}
        with self.assertRaisesRegex(model_dirs.ModelDirError, 'not a hammerspace copy'):
            self.run_roster(config)
        config['ids'][0]['model']['routes'] = config['ids'][0]['model']['routes'][:2]
        with self.assertRaisesRegex(model_dirs.ModelDirError, '2 files'):
            self.run_roster(config)


class ParseModelTests(unittest.TestCase):
    def test_model_key(self):
        c, = ids.parse_ids({'ids': [{'id': '0x66', 'template': '0x00',
                                     'model': {'from': '0x0D', 'routes': [[16, 4], [[32, 8], [48, 8], [32, 8]]]}}]})
        self.assertEqual(c.model, ids.ModelSpec(0x0D, (((16, 4),) * 3, ((32, 8), (48, 8), (32, 8)))))
        self.assertEqual(c.model_source, 0x0D)
        for bad in ({'routes': []}, {'from': '0x4D'}, {'from': '0x00', 'routes': [[1]]}):
            with self.subTest(bad=bad), self.assertRaises(ids.IdConfigError):
                ids.parse_ids({'ids': [{'id': '0x66', 'template': '0x00', 'model': bad}]})


class HammerspaceHelperDirectoryTests(ModelDirHarness):
    def test_output_dol_walkers_see_own_directories(self):
        dol_bytes, *_ = self.run_roster(TWO)
        image = dolfile.DolImage(dol_bytes)
        path = os.path.join(self.tmp, 'main.dol')
        with open(path, 'wb') as f:
            f.write(dol_bytes)
        starts = hh._extraDirStarts(path)
        self.assertEqual(starts, [image.offset_of(p) for p in dirtable.pointers(image)[172:]])
        with open(path, 'rb') as f:
            f.seek(starts[0])
            self.assertEqual(struct.unpack('>I', f.read(4))[0], hh._DAT_FNAME_PTR)
        self.assertEqual(hh._extraDirStarts(self.input_dat), [])          # no DOL at all


if __name__ == '__main__':
    unittest.main()
