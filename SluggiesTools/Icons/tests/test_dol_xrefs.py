import unittest

from SluggiesTools.Icons import dol_map, dol_xrefs
from SluggiesTools.Icons.tests.test_dol_map import (
    BLR, NOP, TEXT_ADDRESS, addi, bl, lis, lwz, make_dol, ori,
)


def li(target, value):
    return addi(target, 0, value)


def mr(target, source):
    return (31 << 26) | (source << 21) | (target << 16) | (source << 11) | (444 << 1)


def b(source, target):
    return (18 << 26) | ((target - source) & 0x3FFFFFC)


def cmpwi(crf, register, value):
    return (11 << 26) | (crf << 23) | (register << 16) | (value & 0xFFFF)


def cmplwi(crf, register, value):
    return (10 << 26) | (crf << 23) | (register << 16) | (value & 0xFFFF)


def blt(crf, offset=8):
    return (16 << 26) | (12 << 21) | ((crf * 4) << 16) | (offset & 0xFFFC)


def mtctr(register):
    return 0x7C0903A6 | (register << 21)


STWU_R1 = (37 << 26) | (1 << 21) | (1 << 16) | 0xFFF0
WHEEL = 0x80631550
WHEEL_END = WHEEL + 0x328


def site(index):
    return TEXT_ADDRESS + 4 * index


class AddressRefTests(unittest.TestCase):
    def refs(self, words, low=WHEEL, high=WHEEL_END, **kwargs):
        dol = make_dol(words)
        return dol_xrefs.find_address_refs(dol, dol_map.parse_header(dol), low, high, **kwargs)

    def test_plain_pair_and_interior_value(self):
        found = self.refs([lis(3, 0x8063), addi(3, 3, 0x1550), lis(4, 0x8063), lwz(0, 4, 0x1556)])
        self.assertEqual(found, [
            dol_xrefs.AddressRef(site(0), site(1), 3, WHEEL),
            dol_xrefs.AddressRef(site(2), site(3), 0, WHEEL + 6),
        ])

    def test_callee_saved_register_survives_a_call_and_a_branch(self):
        words = [lis(30, 0x8063), bl(site(1), site(0)), b(site(2), site(4)), NOP, addi(26, 30, 0x1550)]
        self.assertEqual([(r.lis_site, r.low_site) for r in self.refs(words)], [(site(0), site(4))])

    def test_volatile_register_dies_at_a_call(self):
        self.assertEqual(self.refs([lis(3, 0x8063), bl(site(1), site(0)), addi(3, 3, 0x1550)]), [])

    def test_blr_ends_every_pending_lis(self):
        self.assertEqual(self.refs([lis(30, 0x8063), BLR, addi(3, 30, 0x1550)]), [])

    def test_mr_copies_the_pending_lis(self):
        found = self.refs([lis(31, 0x8063), mr(29, 31), addi(3, 29, 0x1550)])
        self.assertEqual([(r.lis_site, r.register) for r in found], [(site(0), 3)])

    def test_redefinition_and_ori(self):
        self.assertEqual(self.refs([lis(3, 0x8063), li(3, 0), addi(3, 3, 0x1550)]), [])
        found = self.refs([lis(5, 0x8063), ori(5, 5, 0x1550)])
        self.assertEqual([r.value for r in found], [WHEEL])

    def test_window_and_range(self):
        far = [lis(30, 0x8063)] + [NOP] * 4 + [addi(3, 30, 0x1550)]
        self.assertEqual(self.refs(far, window=4), [])
        self.assertEqual(len(self.refs(far, window=5)), 1)
        self.assertEqual(self.refs([lis(3, 0x8063), addi(3, 3, 0x1878)]), [])


class FunctionStartTests(unittest.TestCase):
    def test_prologue_and_previous_blr(self):
        words = [NOP, STWU_R1, NOP, NOP, BLR, NOP, NOP]
        dol = make_dol(words)
        header = dol_map.parse_header(dol)
        self.assertEqual(dol_xrefs.function_start(dol, header, site(3)), site(1))
        self.assertEqual(dol_xrefs.function_start(dol, header, site(6)), site(5))
        self.assertEqual(dol_xrefs.function_start(dol, header, site(0)), TEXT_ADDRESS)

    def test_rejects_data_addresses(self):
        dol = make_dol([BLR])
        with self.assertRaises(dol_xrefs.DolXrefError):
            dol_xrefs.function_start(dol, dol_map.parse_header(dol), 0x80010000)


class BoundTests(unittest.TestCase):
    def classify(self, words):
        dol = make_dol(words)
        header = dol_map.parse_header(dol)
        return {
            (compare.site, compare.value): dol_xrefs.classify_compare(dol, header, compare)
            for compare in dol_xrefs.find_compares(dol, header, (0, 0x4D, 0x64, 0x65))
        }

    def test_is_player_range_check(self):
        words = [cmpwi(1, 0, 0), blt(1), cmpwi(1, 0, 0x4D), NOP]
        self.assertEqual(self.classify(words)[(site(2), 0x4D)], 'is_player')

    def test_is_mii_range_check_names_both_halves(self):
        words = [cmpwi(1, 0, 0x4D), blt(1), cmpwi(1, 0, 0x64), NOP]
        kinds = self.classify(words)
        self.assertEqual(kinds[(site(0), 0x4D)], 'is_mii')
        self.assertEqual(kinds[(site(2), 0x64)], 'is_mii')

    def test_other_register_or_field_is_not_a_range_check(self):
        words = [cmpwi(1, 0, 0), blt(1), cmpwi(1, 5, 0x4D), cmpwi(0, 3, 0x65)]
        kinds = self.classify(words)
        self.assertEqual(kinds[(site(2), 0x4D)], 'other')
        self.assertEqual(kinds[(site(3), 0x65)], 'other')

    def test_compares_decode_signed_and_logical(self):
        dol = make_dol([cmpwi(0, 3, -1), cmplwi(2, 4, 0xFFFF), cmplwi(0, 4, 0x4D)])
        found = dol_xrefs.find_compares(dol, dol_map.parse_header(dol), (-1, 0xFFFF, 0x4D))
        self.assertEqual(found, [
            dol_xrefs.Compare(site(0), 0, 3, -1, False),
            dol_xrefs.Compare(site(1), 2, 4, 0xFFFF, True),
            dol_xrefs.Compare(site(2), 0, 4, 0x4D, True),
        ])

    def test_counted_loops(self):
        words = [li(0, 0x4D), NOP, mtctr(0), li(0, 0x47), li(0, 1), mtctr(0), li(5, 0x65)]
        dol = make_dol(words)
        found = dol_xrefs.find_counted_loops(dol, dol_map.parse_header(dol), (0x47, 0x4D, 0x65))
        self.assertEqual(found, [dol_xrefs.CountedLoop(site(0), 0, 0x4D)])


class ReportTests(unittest.TestCase):
    def test_report_counts_wheel_refs_and_functions(self):
        words = [STWU_R1, lis(3, 0x8063), addi(3, 3, 0x1550), BLR,
                 STWU_R1, lis(4, 0x8063), addi(4, 4, 0x1558), cmpwi(0, 4, 0x65), BLR]
        dol = make_dol(words)
        header = dol_map.parse_header(dol)
        wheel = dol_xrefs.ID_TABLES[0]
        self.assertEqual(wheel.name, 'color_wheel')
        self.assertEqual(len(dol_xrefs.table_refs(dol, header, wheel)), 2)
        # The known-site check needs the real DOL's text; skip it here.
        original = dol_xrefs.check_known_sites
        dol_xrefs.check_known_sites = lambda *_: {}
        try:
            report = dol_xrefs.build_report(dol, header)
        finally:
            dol_xrefs.check_known_sites = original
        table = report['tables']['color_wheel']
        self.assertEqual(table['functions'], [site(0), site(4)])
        self.assertEqual(table['interior_refs'], 1)
        self.assertEqual([c['kind'] for c in report['compares'][0x65]], ['other'])


if __name__ == '__main__':
    unittest.main()
