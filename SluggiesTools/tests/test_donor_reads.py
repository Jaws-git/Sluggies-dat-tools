"""HammerspaceMain's donor reader: one read of the DOL entry per build, the file for everything else."""

import builtins
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
for _path in (TOOLS_DIR, TOOLS_DIR / 'Hammerspace', TOOLS_DIR / 'tests'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import HammerspaceMain as main  # noqa: E402
import synthetic_donor  # noqa: E402


class ReadDonorTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.dat = self.root / 'dt_na.dat'
        self.dat.write_bytes(bytes(range(256)) * 4)
        self.enterContext(mock.patch.object(main.hh, 'INPUT_DAT', str(self.dat)))
        self.enterContext(mock.patch.object(main.hh, 'BASE_SIZE', 0x10000))

    def test_reads_the_file_when_nothing_is_loaded(self):
        donor = main.Donor.in_dat(0x10, 0x40)
        self.assertIsNone(donor.data)
        self.assertEqual((donor.model_offset, donor.model_length, donor.embedded), (0x10, 0x40, False))
        self.assertEqual(donor.read(0x10, 4), bytes([0x10, 0x11, 0x12, 0x13]))
        self.assertEqual(len(donor.read(1020, 16)), 4)                    # short at the end of the file

    def test_loaded_entry_serves_the_ranges_it_holds(self):
        donor = main.Donor.load(0x100, 0x80)
        self.assertEqual((donor.base, donor.data), (0x100, self.dat.read_bytes()[0x100:0x180]))
        with mock.patch.object(builtins, 'open', side_effect=AssertionError('the file was opened')):
            self.assertEqual(donor.read(0x100, 0x80), self.dat.read_bytes()[0x100:0x180])
            self.assertEqual(donor.read(0x17C, 4), self.dat.read_bytes()[0x17C:0x180])
        # before it, past it, or only partly inside: the file
        for offset, length in ((0xFC, 8), (0x180, 4), (0x17C, 8)):
            with self.subTest(offset=offset):
                self.assertEqual(donor.read(offset, length), self.dat.read_bytes()[offset:offset + length])

    def test_unreadable_or_short_entry_is_not_kept(self):
        self.assertIsNone(main.Donor.load(1000, 100).data)                # runs past the end of the file
        self.assertIsNone(main.Donor.load(0, 0).data)
        with mock.patch.object(main.hh, 'INPUT_DAT', str(self.root / 'none.dat')):
            self.assertIsNone(main.Donor.load(0, 16).data)

    def test_embedded_entry_never_reads_the_file(self):
        donor = main.Donor.of_entry(0x40, b'\xBB' * 64)
        self.assertTrue(donor.embedded)
        self.assertEqual((donor.model_offset, donor.model_length), (0x40, 64))
        with mock.patch.object(builtins, 'open', side_effect=AssertionError('the file was opened')):
            self.assertEqual(donor.read(0x48, 4), b'\xBB' * 4)
            self.assertEqual(len(donor.read(0x7C, 16)), 4)                # short at the end of the entry
            for offset in (0x3F, 0x80):
                with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, 'outside the embedded donor'):
                    donor.read(offset, 4)

    def test_for_model_keeps_the_source(self):
        entry = main.Donor.load(0x100, 0x80)
        donor = entry.for_model(0x120, 0x40)
        self.assertEqual((donor.base, donor.data, donor.embedded), (entry.base, entry.data, False))
        self.assertEqual((donor.model_offset, donor.model_length), (0x120, 0x40))

    def test_header_and_section_offsets(self):
        self.dat.write_bytes(struct.pack('>8I', 0, 0x20, 0x40, 0x60, 0x80, 0, 0xA0, 0) + bytes(0x100))
        donor = main.Donor.in_dat(0)
        self.assertEqual(donor.header(), (0, 0x20, 0x40, 0x60, 0x80, 0, 0xA0, 0))
        self.assertEqual(donor.gpl_absolute(), 0x20)
        self.assertEqual(donor.act_absolute(), 0x40)
        with self.assertRaises(IOError):
            main.Donor.in_dat(0x120 - 2).gpl_absolute()
        self.assertEqual(main.Donor.in_dat(0x120 - 2).act_absolute(), 0)   # no whole header: no ACT


class EmbeddedDonorCloneTests(unittest.TestCase):
    """A section clone called on its own reads the donor it is given, never a DAT."""

    def test_clones_read_the_embedded_entry(self):
        block = (struct.pack('>8I', 0, 0x20, 0x24, 0x28, 0x2C, 0, 0x30, 0)
                 + b'GPL!' + b'ACT!' + b'TEX!' + b'SKN!' + b'TAIL')
        donor = main.Donor.of_entry(0x5000, bytes(8) + block).for_model(0x5008, len(block))
        with mock.patch.object(builtins, 'open', side_effect=AssertionError('a file was opened')):
            self.assertEqual(main.CloneHEADER(donor), block[:0x20])
            self.assertEqual(main.CloneGPL(donor), b'GPL!')
            self.assertEqual(main.CloneACT(donor), b'ACT!')
            self.assertEqual(main.CloneTEX(donor), b'TEX!')
            self.assertEqual(main.CloneSKN(donor), b'SKN!')
            self.assertEqual(main.CloneTrailingSections(donor), (b'TAIL', 0x30))
            self.assertEqual(donor.gpl_absolute(), 0x5028)


class BuildReadsTheDonorOnceTests(unittest.TestCase):
    def test_a_build_opens_the_input_dat_once(self):
        env = self.enterContext(synthetic_donor.donor_environment())
        data = env.reload()
        data['SluggiesModel']['UseHammerspace'] = True
        input_dat = main.hh.INPUT_DAT
        real_open = builtins.open
        opened = []

        def counting_open(path, *args, **kwargs):
            if str(path) == str(input_dat):
                opened.append(path)
            return real_open(path, *args, **kwargs)

        modes = main.SectionModes(gpl='build', tex='build', skn='build')
        with mock.patch.object(builtins, 'open', side_effect=counting_open):
            build = main.BuildModelBlock(data, modes, sluggie_path=env.sluggie_path)
        self.assertTrue(build.validation_report['valid'], build.validation_report.get('errors'))
        self.assertEqual(len(opened), 1)


if __name__ == '__main__':
    unittest.main()
