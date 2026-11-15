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
        self.addCleanup(setattr, main, '_PRELOADED_DONOR', None)

    def test_reads_the_file_without_a_preload(self):
        self.assertEqual(main._read_donor(0x10, 4), bytes([0x10, 0x11, 0x12, 0x13]))
        self.assertEqual(len(main._read_donor(1020, 16)), 4)              # short at the end of the file

    def test_preloaded_entry_serves_the_ranges_it_holds(self):
        main._preload_donor(0x100, 0x80)
        self.assertEqual(main._PRELOADED_DONOR, (0x100, self.dat.read_bytes()[0x100:0x180]))
        with mock.patch.object(builtins, 'open', side_effect=AssertionError('the file was opened')):
            self.assertEqual(main._read_donor(0x100, 0x80), self.dat.read_bytes()[0x100:0x180])
            self.assertEqual(main._read_donor(0x17C, 4), self.dat.read_bytes()[0x17C:0x180])
        # before it, past it, or only partly inside: the file
        for offset, length in ((0xFC, 8), (0x180, 4), (0x17C, 8)):
            with self.subTest(offset=offset):
                self.assertEqual(main._read_donor(offset, length), self.dat.read_bytes()[offset:offset + length])

    def test_unreadable_or_short_entry_is_not_kept(self):
        main._preload_donor(1000, 100)                                    # runs past the end of the file
        self.assertIsNone(main._PRELOADED_DONOR)
        with mock.patch.object(main.hh, 'INPUT_DAT', str(self.root / 'none.dat')):
            main._preload_donor(0, 16)
        self.assertIsNone(main._PRELOADED_DONOR)

    def test_embedded_donor_wins_over_a_preload(self):
        main._PRELOADED_DONOR = (0, b'\xAA' * 64)
        with mock.patch.object(main, '_EMBEDDED_DONOR', (0, b'\xBB' * 64)):
            self.assertEqual(main._read_donor(8, 4), b'\xBB' * 4)
            main._preload_donor(0, 16)
            self.assertIsNone(main._PRELOADED_DONOR)

    def test_header_and_gpl_offset(self):
        self.dat.write_bytes(struct.pack('>8I', 0, 0x20, 0x40, 0x60, 0x80, 0, 0xA0, 0) + bytes(0x100))
        self.assertEqual(main._donor_header(0), (0, 0x20, 0x40, 0x60, 0x80, 0, 0xA0, 0))
        self.assertEqual(main._donor_gpl_absolute(0), 0x20)
        self.assertEqual(main._act_section_absolute(0), 0x40)
        with self.assertRaises(IOError):
            main._donor_gpl_absolute(0x120 - 2)


class BuildReadsTheDonorOnceTests(unittest.TestCase):
    def test_a_build_opens_the_input_dat_once_and_leaves_no_preload(self):
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

        modes = main.SectionModes(gpl='build', act='clone', tex='build', skn='build', trailing='clone')
        with mock.patch.object(builtins, 'open', side_effect=counting_open):
            build = main.BuildModelBlock(data, modes, sluggie_path=env.sluggie_path)
        self.assertTrue(build.validation_report['valid'], build.validation_report.get('errors'))
        self.assertEqual(len(opened), 1)
        self.assertIsNone(main._PRELOADED_DONOR)


if __name__ == '__main__':
    unittest.main()
