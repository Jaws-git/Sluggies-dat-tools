import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import act_rebuild
import HammerspaceHelper as hh
import LodPartnerGuard as guard

GEO_NONE = 0xFFFF


def _block(geo_name: str, bone_count: int, owners: dict[int, int], placed=None) -> bytes:
    """A minimal model block: header, then an ACT section with a bone table
    (GeoId per bone) and the geo name right after it. *placed* maps a bone id
    to ``(parent_id, translation_y)``; those bones get a parent link and an
    SRT record, all others have neither."""
    placed = placed or {}
    act = 0x20
    srt_start = 0x20 + bone_count * 0x1C
    table, srts = b'', b''
    for bone in range(bone_count):
        orientation = parent = 0
        if bone in placed:
            parent_id, translation_y = placed[bone]
            parent = 0x20 + parent_id * 0x1C
            orientation = srt_start + len(srts)
            srts += act_rebuild.pack_srt_blob(12, (1, 1, 1), (1, 0, 0, 0), (0, translation_y, 0))
        table += struct.pack(
            '>IIIIIHHBBH', orientation, 0, 0, parent, 0, owners.get(bone, GEO_NONE), bone, 1, 0, 0,
        )
    table += srts
    name_ptr = 0x20 + len(table)
    act_header = struct.pack('>IHHIIIHHII', 0, 0, bone_count, 0, 0x20, name_ptr, 0, 0, 0, 0)
    header = struct.pack('>8I', 0, 0, act, 0, 0, 0, 0, 0)
    return header + act_header + table + geo_name.encode('latin-1') + b'\x00'


HIGH_91 = _block('mario.gpl', 91, {54: 1, 55: 2})
HIGH_92 = _block('mario.gpl', 92, {54: 1, 55: 2})
LOW_ON_NEW_BONE = _block('L_mario.gpl', 92, {54: 1, 55: 2, 91: 3})
LOW_ON_OLD_BONE = _block('L_mario.gpl', 92, {49: 3, 54: 1, 55: 2})


class ActSummaryTests(unittest.TestCase):
    def test_reads_name_bone_count_and_geometry_owners(self):
        summary = guard.act_summary(LOW_ON_NEW_BONE)

        self.assertEqual(summary.geo_name, 'L_mario.gpl')
        self.assertEqual(summary.bone_count, 92)
        self.assertEqual(summary.drawn_bones, {54: 1, 55: 2, 91: 3})
        self.assertTrue(summary.is_low_poly)

    def test_pair_stems_ignore_prefix_and_extension(self):
        # The game leaves the Mii's name unterminated: 'mii_male.gpl' + 'p'.
        high = guard.act_summary(_block('mii_male.gplp', 1, {}))
        low = guard.act_summary(_block('L_mii_male.gpl', 1, {}))

        self.assertEqual(high.geo_name, 'mii_male.gpl')
        self.assertEqual(high.stem, low.stem)
        self.assertFalse(high.is_low_poly)

    def test_leftover_byte_after_the_extension_is_dropped(self):
        summary = guard.act_summary(_block('L_teresa.gpl', 1, {}))

        self.assertEqual(summary.geo_name, 'L_teresa.gpl')

    def test_block_without_act_section_has_no_summary(self):
        self.assertIsNone(guard.act_summary(bytes(0x40)))


class LodPartnerErrorsTests(unittest.TestCase):
    def _errors(self, new_block, file_index, partner_block):
        with (
            mock.patch.object(guard, '_entry_exists', return_value=True),
            mock.patch.object(guard, 'read_current_block', return_value=partner_block),
        ):
            return guard.lod_partner_errors(new_block, 18, file_index)

    def test_low_poly_drawing_on_a_bone_the_high_poly_lacks_is_refused(self):
        errors = self._errors(LOW_ON_NEW_BONE, 1, HIGH_91)

        self.assertEqual(len(errors), 1)
        self.assertIn('bone(s) 91 (submesh 3)', errors[0])
        self.assertIn('mario.gpl (chunk 18, file 0)', errors[0])

    def test_low_poly_drawing_on_a_bone_the_high_poly_also_has_is_allowed(self):
        self.assertEqual(self._errors(LOW_ON_NEW_BONE, 1, HIGH_92), [])

    def test_low_poly_with_an_empty_extra_bone_is_allowed(self):
        # Dolphin, 2026-09-27: an unused new bone past the HP skeleton loads.
        self.assertEqual(self._errors(LOW_ON_OLD_BONE, 1, HIGH_91), [])

    def test_high_poly_dropping_a_bone_the_low_poly_draws_on_is_refused(self):
        errors = self._errors(HIGH_91, 0, LOW_ON_NEW_BONE)

        self.assertEqual(len(errors), 1)
        self.assertIn('would have only 91 bones', errors[0])
        self.assertIn('L_mario.gpl (chunk 18, file 1)', errors[0])

    def test_high_poly_drawing_on_a_bone_the_low_poly_lacks_is_allowed(self):
        # Dolphin, 2026-09-18: HP Mario with a submesh on a new bone 91, L_mario vanilla.
        high = _block('mario.gpl', 92, {54: 1, 55: 2, 91: 3})

        self.assertEqual(self._errors(high, 0, _block('L_mario.gpl', 91, {54: 1, 55: 2})), [])

    def test_neighbour_of_another_character_is_not_a_partner(self):
        self.assertEqual(self._errors(LOW_ON_NEW_BONE, 1, _block('luigi.gpl', 10, {})), [])

    def test_unreadable_partner_skips_the_check(self):
        with mock.patch.object(guard, '_entry_exists', side_effect=OSError('no DOL')):
            self.assertEqual(guard.lod_partner_errors(LOW_ON_NEW_BONE, 18, 1), [])


class LodPartnerWarningsTests(unittest.TestCase):
    """Bone 91 is the added bone: both models vanilla-count 91."""

    def _warnings(self, new_block, file_index, partner_block):
        with (
            mock.patch.object(guard, '_entry_exists', return_value=True),
            mock.patch.object(guard, 'read_current_block', return_value=partner_block),
            mock.patch.object(guard, '_vanilla_bone_count', return_value=91),
        ):
            return guard.lod_partner_warnings(new_block, 18, file_index)

    def test_added_bone_placed_identically_is_quiet(self):
        low = _block('L_mario.gpl', 92, {91: 3}, placed={91: (51, 0.255)})
        high = _block('mario.gpl', 92, {}, placed={91: (51, 0.255 + 1e-6)})

        self.assertEqual(self._warnings(low, 1, high), [])

    def test_added_bone_on_a_different_parent_warns(self):
        # Dolphin, 2026-09-27: L_mario's submesh followed HP Mario's bone 91 to the arm.
        low = _block('L_mario.gpl', 92, {91: 3}, placed={91: (51, 0.255)})
        high = _block('mario.gpl', 92, {}, placed={91: (62, 0.105)})

        warnings = self._warnings(low, 1, high)

        self.assertEqual(len(warnings), 1)
        self.assertIn('parent 62 in mario.gpl vs 51 in L_mario.gpl', warnings[0])
        self.assertIn('different SRT', warnings[0])
        self.assertIn("geometry on it (submesh 3) follows mario.gpl's bone 91", warnings[0])

    def test_warns_when_patching_the_high_poly_side_too(self):
        low = _block('L_mario.gpl', 92, {}, placed={91: (51, 0.255)})
        high = _block('mario.gpl', 92, {}, placed={91: (51, 0.5)})

        warnings = self._warnings(high, 0, low)

        self.assertEqual(len(warnings), 1)
        self.assertNotIn('parent', warnings[0])
        self.assertIn('anything L_mario.gpl draws on it would follow', warnings[0])

    def test_vanilla_bones_and_one_sided_bones_are_not_compared(self):
        # Bone 50 differs but is vanilla; bone 92 exists only in the low-poly model.
        low = _block('L_mario.gpl', 93, {}, placed={50: (1, 0.1), 92: (51, 0.2)})
        high = _block('mario.gpl', 92, {}, placed={50: (1, 0.3)})

        self.assertEqual(self._warnings(low, 1, high), [])


class EntryExistsTests(unittest.TestCase):
    def test_stops_at_the_next_chunks_entry_run(self):
        entry = struct.pack('>12I', hh._DAT_FNAME_PTR, *([0] * 11))
        # Chunk 0 has two entries, chunk 1 starts right after them.
        dol = entry * 3 + b'\x00' * 48
        with tempfile.TemporaryDirectory() as temp_dir:
            dol_path = pathlib.Path(temp_dir) / 'main.dol'
            dol_path.write_bytes(dol)
            with (
                mock.patch.object(hh, 'OUTPUT_DOL', str(dol_path)),
                mock.patch.object(hh, '_readDirPtrs', return_value=[0, 96]),
            ):
                self.assertTrue(guard._entry_exists(0, 1))
                self.assertFalse(guard._entry_exists(0, 2))
                self.assertTrue(guard._entry_exists(1, 0))
                self.assertFalse(guard._entry_exists(1, 1))
                self.assertFalse(guard._entry_exists(0, -1))


if __name__ == '__main__':
    unittest.main()
