"""Export as .sluggie (``Roster/slot_export.py``) and the embedded donor it relies on (``HammerspaceMain``).

No production data: the block is ``synthetic_donor``'s, read back with the export reader and rebuilt by the
Hammerspace builder from the ``.sluggie`` alone (the ``1_Input`` DAT the builder would read holds zeros there).
"""

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
for _path in (TOOLS_DIR, TOOLS_DIR / 'Hammerspace', TOOLS_DIR / 'Roster', pathlib.Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import synthetic_donor  # noqa: E402
import HammerspaceHelper as hh  # noqa: E402
import HammerspaceMain as main  # noqa: E402
import gui_grid  # noqa: E402
from binfmt import encode_field  # noqa: E402
from Roster import slot_export  # noqa: E402

BLOCK_BASE = synthetic_donor.MODEL_OFFSET


def donor_block():
    """``(sluggie, block)`` of the synthetic donor."""
    data, dat, length = synthetic_donor.build_donor()
    return data, dat[BLOCK_BASE:BLOCK_BASE + length]


class NameTests(unittest.TestCase):
    def test_folder_and_sluggie_names(self):
        self.assertEqual(slot_export.folder_name('Fire Mario', 1), 'Custom Fire Mario 01')
        self.assertEqual(slot_export.tag('Fire Mario', 12), 'FireMario12')
        self.assertEqual(slot_export.tag('?!', 1), 'Slot01')
        self.assertEqual(slot_export.sluggie_name('78277664_mario.gpl', 'FireMario01'),
                         '78277664_mario_FireMario01.gpl.sluggie')
        self.assertEqual(slot_export.sluggie_name('78711424_L_mario.gpl', 'Mario02'),
                         '78711424_L_mario_Mario02.gpl.sluggie')
        self.assertEqual(slot_export.sluggie_name('78798592_glove_L.tpl', 'Mario01'),
                         '78798592_glove_L_Mario01.tpl.sluggie')

    def test_safe_name(self):
        self.assertEqual(slot_export.safe_name('Mr. L: "Green"/?'), 'Mr. L Green')
        self.assertEqual(slot_export.safe_name('  Bowser   Jr.  '), 'Bowser Jr')
        self.assertEqual(slot_export.safe_name(None), '')

    def test_character_name_as_the_grid_shows_it(self):
        name = slot_export.character_name
        self.assertEqual(name({'id': 0, 'name': {'en': 'Mario'}, 'default_name': None}), 'Mario')
        # a spare row whose table text is the stock "#N/A"
        self.assertEqual(name({'id': 0x47, 'name': {'en': '#N/A'}, 'default_name': 'Black Yoshi'}), 'Black Yoshi')
        self.assertEqual(name({'id': 0x66, 'name': {'en': '-'}, 'default_name': None}), 'Slot 0x66')
        self.assertEqual(name({'id': 0x66, 'name': None, 'default_name': None}), 'Slot 0x66')

    def test_pick_number(self):
        models = ['78277664_mario.gpl', '78711424_L_mario.gpl']
        free = lambda _folder: False
        self.assertEqual(slot_export.pick_number('Mario', models, set(), free), 1)
        taken_folders = {'Custom Mario 01', 'Custom Mario 02'}
        self.assertEqual(slot_export.pick_number('Mario', models, set(), taken_folders.__contains__), 3)
        # a file name already in 2_Output_Models (another folder of the same name moved away), any case
        self.assertEqual(slot_export.pick_number('Mario', models, {'78711424_l_mario_mario01.gpl.sluggie'}, free), 2)


class EmbeddedDonorTests(unittest.TestCase):
    """``DonorEntry``: the builder reads the donor from the .sluggie, never from the DAT or the DOL route."""

    def setUp(self):
        self.data, self.block = donor_block()
        self.data['SluggiesModel']['DonorEntry'] = {'Offset': hex(BLOCK_BASE), 'Data': encode_field(self.block, True)}
        temp = self.enterContext(tempfile.TemporaryDirectory())
        blank = pathlib.Path(temp) / 'dt_na.dat'
        blank.write_bytes(bytes(BLOCK_BASE + len(self.block)))           # zeros where the donor would be
        self.enterContext(mock.patch.object(hh, 'INPUT_DAT', str(blank)))
        self.enterContext(mock.patch.object(hh, 'OUTPUT_DAT', str(blank)))
        self.enterContext(mock.patch.object(hh, 'readDolEntry', return_value=(-1, -1)))

    def test_donor_entry(self):
        self.assertEqual(main.donor_entry(self.data['SluggiesModel']), (BLOCK_BASE, self.block))
        self.assertIsNone(main.donor_entry({'ChunkNumber': 18}))
        with self.assertRaises(ValueError):
            main.donor_entry({'DonorEntry': {'Offset': '0x10'}})

    def test_clone_build_is_the_donor(self):
        build = main.BuildModelBlock(self.data, main.SectionModes())
        self.assertTrue(build.validation_report['valid'])
        self.assertEqual(build.block, self.block)
        self.assertEqual(build.donor_block, self.block)
        self.assertIsNone(main._EMBEDDED_DONOR)                          # reset after the build

    def test_gpl_build_reads_the_embedded_donor(self):
        build = main.BuildModelBlock(self.data, main.SectionModes(gpl='build'))
        self.assertTrue(build.validation_report['valid'], build.validation_report['errors'])

    def test_reads_outside_the_entry_are_refused(self):
        main._EMBEDDED_DONOR = (BLOCK_BASE, self.block)
        try:
            with self.assertRaises(ValueError):
                main._open_source(BLOCK_BASE - 1)
            with self.assertRaises(ValueError):
                main._open_source(BLOCK_BASE + len(self.block))
            with main._open_source(BLOCK_BASE + 0x20) as source:
                source.seek(BLOCK_BASE + 0x20)
                self.assertEqual(source.read(8), self.block[0x20:0x28])
                self.assertEqual(source.tell(), BLOCK_BASE + 0x28)
        finally:
            main._EMBEDDED_DONOR = None


class ExportReaderTests(unittest.TestCase):
    """The slot's block read with the regular export reader, then rebuilt from the .sluggie alone."""

    def setUp(self):
        self.data, self.block = donor_block()
        self.base = 0x40000                                               # where the base model lies in 1_Input

    def read(self):
        return slot_export.read_entry(self.base, len(self.block), slot_export.PlacedBlock(self.base, self.block))

    def test_document_round_trip(self):
        entry = self.read()
        models = slot_export.models_of(entry)
        self.assertEqual(len(models), 1)
        meta = {'Character': '0x00', 'Name': 'Mario', 'Exported': '2026-10-08'}
        doc = slot_export.document(models[0], 18, 0, self.base, self.block, meta)
        model = doc['SluggiesModel']
        keys = list(model)
        self.assertEqual(keys[keys.index('ModelLength') + 1], 'SlotExport')      # the Maintenance scan's marker
        self.assertEqual((model['ChunkNumber'], model['FileIndex'], model['ModelOffset']), (18, 0, hex(self.base)))
        self.assertTrue(model['UseHammerspace'])
        self.assertEqual(main.donor_entry(model), (self.base, self.block))
        doc = json.loads(json.dumps(doc))
        with tempfile.TemporaryDirectory() as temp:
            blank = pathlib.Path(temp) / 'dt_na.dat'
            blank.write_bytes(bytes(16))
            with (mock.patch.object(hh, 'INPUT_DAT', str(blank)), mock.patch.object(hh, 'OUTPUT_DAT', str(blank)),
                  mock.patch.object(hh, 'readDolEntry', return_value=(-1, -1))):
                build = main.BuildModelBlock(doc, main.SectionModes())
        self.assertTrue(build.validation_report['valid'])
        self.assertEqual(build.block, self.block)

    def test_shifted_file_reads_the_route(self):
        # the slot's file lies at output offset 0x100 of the DAT, read as if it lay at its base offset
        dat = io.BytesIO(b'\xAA' * 0x100 + self.block + b'\xBB' * 8)
        view = slot_export.ShiftedFile(dat, 0x100 - self.base)
        view.seek(self.base + 4)
        self.assertEqual(view.read(4), self.block[4:8])
        self.assertEqual(view.tell(), self.base + 8)
        view.seek(self.base + len(self.block))
        self.assertEqual(view.read(2), b'\xBB\xBB')                     # past the entry: the DAT's bytes


class GuiTests(unittest.TestCase):
    def test_command_and_result(self):
        self.assertEqual(gui_grid.export_command(0x4A), ('--export-slot', '0x4A'))
        self.assertNotIn('--export-slot', gui_grid.WRITING_FLAGS)          # reads the game only: no re-read
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(gui_grid.export_result(root))
            path = os.path.join(root, gui_grid.SLOT_EXPORT_REL)
            os.makedirs(os.path.dirname(path))
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'id': '0x4A', 'folder': 'X/Custom Mario 01'}, f)
            self.assertEqual(gui_grid.export_result(root)['folder'], 'X/Custom Mario 01')
        self.assertEqual(os.path.basename(gui_grid.SLOT_EXPORT_REL), slot_export.RESULT_FILE)

    def test_summary_dialog(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Custom Mario 01')
            os.makedirs(os.path.join(folder, 'sub'))
            for rel, size in (('a.sluggie', 1024 * 1024), (os.path.join('sub', 'b.png'), 512 * 1024)):
                with open(os.path.join(folder, rel), 'wb') as f:
                    f.write(bytes(size))
            self.assertEqual(gui_grid.folder_size(folder), 1536 * 1024)
            ok = gui_grid.export_summary_dialog(None, 0x4A, 0, {'folder': folder})
            text = [t for t, _k in ok.lines]
            self.assertEqual(ok.title, 'Export finished')
            self.assertFalse(ok.can_apply)
            self.assertIn('Exported 0x4A (0x4A).', text)
            self.assertIn(f'Folder: {os.path.normpath(folder)}', text)
            self.assertIn('Total size: 1.50 MB', text)
        aborted = gui_grid.export_summary_dialog(None, 0x4A, 1, {'id': '0x4A', 'error': 'dt_na.dat is missing'})
        self.assertEqual(aborted.title, 'Export aborted')
        self.assertIn('Reason: dt_na.dat is missing', [t for t, _k in aborted.lines])
        crashed = gui_grid.export_summary_dialog(None, 0x4A, 1, None)
        self.assertEqual(crashed.title, 'Export aborted')

    def test_failed_export_writes_its_reason(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(slot_export.main(['0x4A', '--output-dir', out, '--models-dir', out]), 1)
            with open(slot_export.result_path(out), 'r', encoding='utf-8') as f:
                result = json.load(f)
        self.assertEqual(result['id'], '0x4A')
        self.assertIn('dt_na.dat', result['error'])
        self.assertNotIn('folder', result)

    def test_keys_match(self):
        self.assertEqual(slot_export.DONOR_KEY, main.DONOR_ENTRY_KEY)


if __name__ == '__main__':
    unittest.main()
