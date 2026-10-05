"""A model patched into another character's slot (``SlotTarget``).

A synthetic input with three character directories, each holding a high-poly
model (file 0) and its ``L_`` partner (file 1): the source (Red Toad
stand-in), a target with the same skeleton (Blue Toad stand-in) and one with
another skeleton. Everything runs on real HammerspaceHelper file I/O in a temp
directory; only the debug dumps are stubbed out.
"""
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import act_rebuild
import HammerspaceHelper as hh
import HammerspaceMain as main
import LodPartnerGuard
import SlotTarget

from SluggiesTools.Roster import slots
from SluggiesTools.tests.test_roster_state import build as build_roster

SOURCE, TARGET, OTHER = 30, 31, 32
BASE_SIZE = 0x8000
EMPTY_PTR = 0x10
TOAD = [None, 0, 1, 1]            # parent per bone
OTHER_SKELETON = [None, 0, 0, 2]


def model_block(geo_name: str, parents: list, drawn: dict | None = None, fill: int = 0) -> bytes:
    """A model block with just the ACT section LodPartnerGuard.act_summary reads."""
    header, record = act_rebuild.HEADER_SIZE, act_rebuild.BONE_RECORD_SIZE
    act = 0x20
    name_at = header + len(parents) * record
    block = bytearray(act + name_at + 0x20)
    struct.pack_into('>I', block, 0x08, act)
    struct.pack_into('>H', block, act + 0x06, len(parents))
    struct.pack_into('>I', block, act + 0x10, name_at)
    block[act + name_at:act + name_at + len(geo_name)] = geo_name.encode()
    for bone, parent in enumerate(parents):
        parent_ptr = 0 if parent is None else header + parent * record
        geo_id = (drawn or {}).get(bone, 0xFFFF)
        struct.pack_into('>IIIIIHH', block, act + header + bone * record, 0, 0, 0, parent_ptr, 0, geo_id, bone)
    block += bytes([fill]) * (-len(block) % 0x20 or 0x20)
    return bytes(block)


VANILLA = {
    (SOURCE, 0): model_block('kinopio_r.gpl', TOAD, fill=1),
    (SOURCE, 1): model_block('L_kinopio_r.gpl', TOAD, {3: 0}, fill=2),
    (TARGET, 0): model_block('kinopio_b.gpl', TOAD, fill=3),
    (TARGET, 1): model_block('L_kinopio_b.gpl', TOAD, {3: 0}, fill=4),
    (OTHER, 0): model_block('heiho.gpl', OTHER_SKELETON, fill=5),
    (OTHER, 1): model_block('L_heiho.gpl', OTHER_SKELETON, fill=6),
}


class SlotHarness(unittest.TestCase):
    """Input/output DOL and DAT with the three character directories (module docstring). ``FILES`` / ``VANILLA``:
    the files each directory holds and their blocks (a subclass adds the equipment files 2-5)."""

    FILES = (0, 1)
    VANILLA = VANILLA

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name)

        dol = bytearray(0x100)
        pointers = [EMPTY_PTR] * 120
        self.records, self.vanilla_offsets = {}, {}
        dat = bytearray(0x1000)
        for chunk in (SOURCE, TARGET, OTHER):
            pointers[chunk] = len(dol)
            for file_index in self.FILES:
                block = self.VANILLA[(chunk, file_index)]
                offset = len(dat)
                dat += block
                self.records[(chunk, file_index)] = len(dol)
                self.vanilla_offsets[(chunk, file_index)] = offset
                dol += struct.pack('>12I', *([hh._DAT_FNAME_PTR, len(block), offset, len(block)] * 3))
            dol += b'\x00' * hh._ENTRY_SIZE
        dat += b'\x00' * (BASE_SIZE - len(dat))

        self.input_dol, self.input_dat = root / 'in.dol', root / 'in.dat'
        self.output_dol, self.output_dat = root / 'out.dol', root / 'out.dat'
        self.input_dol.write_bytes(dol)
        self.input_dat.write_bytes(dat)
        shutil.copyfile(self.input_dol, self.output_dol)
        shutil.copyfile(self.input_dat, self.output_dat)

        for patcher in (
            mock.patch.object(hh, 'INPUT_DOL', str(self.input_dol)),
            mock.patch.object(hh, 'INPUT_DAT', str(self.input_dat)),
            mock.patch.object(hh, 'OUTPUT_DOL', str(self.output_dol)),
            mock.patch.object(hh, 'OUTPUT_DAT', str(self.output_dat)),
            mock.patch.object(hh, '_FST_INPUT', str(root / 'missing_fst.bin')),
            mock.patch.object(hh, '_FST_OUTPUT', str(root / 'fst.bin')),
            mock.patch.object(hh, 'BASE_SIZE', BASE_SIZE),
            mock.patch.object(hh, '_readDirPtrs', return_value=pointers),
            mock.patch.object(hh, 'writeDebugDumps'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- helpers ----------------------------------------------------------

    def route(self, route):
        words = struct.unpack_from('>12I', self.output_dol.read_bytes(), self.records[route])
        return words[2], words[1]

    def bytes_at(self, offset, length):
        with open(self.output_dat, 'rb') as dat:
            dat.seek(offset)
            return dat.read(length)

    def live(self, route):
        return self.bytes_at(*self.route(route))

    def build(self, block, source=(SOURCE, 0)):
        return main.ModelBlockBuild(
            block=block, parsed=None, chunk_number=source[0], file_index=source[1],
            original_offset=self.vanilla_offsets[source], original_length=len(self.VANILLA[source]),
            section_modes=main.SectionModes(), section_sizes={},
            validation_report={'valid': True},
        )

    def write(self, block, source=(SOURCE, 0), target=None):
        return main.WriteModelBlock(self.build(block, source), 'test.sluggie', target=target)

    def assert_vanilla_route(self, route):
        self.assertEqual(self.route(route), (self.vanilla_offsets[route], len(self.VANILLA[route])))
        self.assertEqual(self.live(route), self.VANILLA[route])



class SlotTargetTests(SlotHarness):
    # -- writes ----------------------------------------------------------------

    def test_block_reaches_the_target_route_and_the_source_is_untouched(self):
        edited = model_block('kinopio_r.gpl', TOAD, fill=0x77)
        offset = self.write(edited, target=SlotTarget.Target(TARGET, 0, 0x13))

        self.assertGreaterEqual(offset, BASE_SIZE)
        self.assertEqual(self.route((TARGET, 0)), (offset, len(edited)))
        self.assertEqual(self.live((TARGET, 0)), edited)
        self.assert_vanilla_route((SOURCE, 0))
        self.assert_vanilla_route((SOURCE, 1))
        self.assert_vanilla_route((TARGET, 1))
        # The target's own vanilla block is no longer used by anything.
        vanilla = self.vanilla_offsets[(TARGET, 0)]
        self.assertEqual(self.bytes_at(vanilla, len(VANILLA[(TARGET, 0)])), bytes(len(VANILLA[(TARGET, 0)])))

    def test_untargeted_write_still_goes_to_the_source_route(self):
        edited = model_block('kinopio_r.gpl', TOAD, fill=0x55)
        offset = self.write(edited)

        self.assertEqual(self.route((SOURCE, 0)), (offset, len(edited)))
        self.assert_vanilla_route((TARGET, 0))

    def test_hp_and_partner_in_order(self):
        self.write(model_block('kinopio_r.gpl', TOAD, fill=0x11), target=SlotTarget.Target(TARGET, 0))
        low = model_block('L_kinopio_r.gpl', TOAD, {3: 0}, fill=0x22)
        self.write(low, source=(SOURCE, 1), target=SlotTarget.Target(TARGET, 1))

        self.assertEqual(self.live((TARGET, 1)), low)
        self.assert_vanilla_route((SOURCE, 1))

    def test_hp_as_low_writes_both_files_as_separate_copies(self):
        high = model_block('kinopio_r.gpl', TOAD, fill=0x33)
        self.write(high, target=SlotTarget.Target(TARGET, 0, as_low=True))

        self.assertEqual(self.live((TARGET, 0)), high)
        self.assertEqual(self.live((TARGET, 1)), high)
        self.assertNotEqual(self.route((TARGET, 0))[0], self.route((TARGET, 1))[0])
        self.assert_vanilla_route((SOURCE, 1))

    # -- guards -------------------------------------------------------------------

    def test_skeleton_guard_refuses_a_different_skeleton(self):
        before = self.output_dol.read_bytes()
        with self.assertRaisesRegex(SlotTarget.TargetError, 'skeletons do not match.*new ID'):
            self.write(model_block('kinopio_r.gpl', TOAD), target=SlotTarget.Target(OTHER, 0))
        self.assertEqual(self.output_dol.read_bytes(), before)

    def test_skeleton_problems_rest_pose_is_only_a_warning(self):
        a = LodPartnerGuard.ActSummary('a.gpl', 2, {}, {0: None, 1: 0}, {0: None, 1: (8, 1.0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0)})
        b = LodPartnerGuard.ActSummary('b.gpl', 2, {}, {0: None, 1: 0}, {0: None, 1: (8, 1.0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 0)})
        errors, warnings = SlotTarget.skeleton_problems(a, b)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)

    def test_lone_low_poly_model_needs_its_own_high_poly_model_in_the_slot(self):
        low = model_block('L_kinopio_r.gpl', TOAD, {3: 0}, fill=0x22)
        with self.assertRaisesRegex(SlotTarget.TargetError, 'kinopio_b.gpl there'):
            self.write(low, source=(SOURCE, 1), target=SlotTarget.Target(TARGET, 1))
        self.assert_vanilla_route((TARGET, 1))

    def test_high_poly_model_over_another_low_poly_model_warns(self):
        with mock.patch.object(main._slogger, 'warning') as warning:
            self.write(model_block('kinopio_r.gpl', TOAD), target=SlotTarget.Target(TARGET, 0))
        self.assertTrue(any('L_kinopio_b.gpl' in call.args[0] for call in warning.call_args_list))

    def test_partner_check_pairs_with_the_target_slot(self):
        self.write(model_block('kinopio_r.gpl', TOAD), target=SlotTarget.Target(TARGET, 0))
        # A low-poly block drawing on an added bone 4 its high-poly partner lacks.
        low = model_block('L_kinopio_r.gpl', TOAD + [3], {4: 0})
        with self.assertRaisesRegex(ValueError, f'chunk {TARGET}, file 0'):
            self.write(low, source=(SOURCE, 1), target=SlotTarget.Target(TARGET, 1))

    def test_low_poly_partner_check_ignores_names_in_a_slot(self):
        # The target's vanilla L draws on bone 3; an HP with only 3 bones is refused
        # although the names (kinopio_r / L_kinopio_b) do not pair.
        found = LodPartnerGuard.find_partner(TARGET, 0, LodPartnerGuard.act_summary(VANILLA[(SOURCE, 0)]), True)
        self.assertEqual(found[0], 1)
        self.assertIsNone(LodPartnerGuard.find_partner(
            TARGET, 0, LodPartnerGuard.act_summary(VANILLA[(SOURCE, 0)])))

    def test_stadiums_and_other_files_are_refused(self):
        with self.assertRaisesRegex(SlotTarget.TargetError, 'not a character directory'):
            SlotTarget.check(VANILLA[(SOURCE, 0)], (9, 0), SlotTarget.Target(TARGET, 0))
        with self.assertRaisesRegex(SlotTarget.TargetError, 'file 2'):
            SlotTarget.check(VANILLA[(SOURCE, 0)], (SOURCE, 2), SlotTarget.Target(TARGET, 0))
        with self.assertRaisesRegex(SlotTarget.TargetError, 'container'):
            SlotTarget.check(VANILLA[(SOURCE, 0)], (SOURCE, 0), SlotTarget.Target(TARGET, 0),
                             {'container_prefix_size': 0x20})

    # -- unpatch -------------------------------------------------------------------

    def test_unpatch_restores_the_target_and_leaves_the_source(self):
        self.write(model_block('kinopio_r.gpl', TOAD, fill=0x44), target=SlotTarget.Target(TARGET, 0, as_low=True))
        self.assertTrue(main.UnpatchTarget(SlotTarget.Target(TARGET, 0, as_low=True)))

        self.assert_vanilla_route((TARGET, 0))
        self.assert_vanilla_route((TARGET, 1))
        self.assert_vanilla_route((SOURCE, 0))
        self.assert_vanilla_route((SOURCE, 1))

    # -- target resolution ---------------------------------------------------------

    def test_make_target_picks_the_file_by_role_and_skips_the_own_route(self):
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)):
            self.assertEqual(SlotTarget.make_target('0x13', (SOURCE, 1)), SlotTarget.Target(TARGET, 1, 0x13))
            with self.assertRaisesRegex(SlotTarget.TargetError, 'High model'):
                SlotTarget.make_target('0x13', (SOURCE, 1), as_low=True)
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x12, SOURCE)):
            self.assertIsNone(SlotTarget.make_target('0x12', (SOURCE, 0)))


OWN = 120                         # stands in for the first directory past the stock table (172 in the game)


class OwnDirectoryTests(SlotHarness):
    """A new ID's own model directory (roster ``model_dirs``) as a slot target. Its records sit past the
    stock table in the output DOL; its files are hammerspace copies of SOURCE's."""

    def setUp(self):
        super().setUp()
        dol = bytearray(self.output_dol.read_bytes())
        dat = bytearray(self.output_dat.read_bytes())
        start = len(dol)
        self.copies = {}
        for file_index in (0, 1):
            block = VANILLA[(SOURCE, file_index)]
            offset = len(dat)
            dat += block + bytes(-len(block) % hh.HS_ALIGN_BYTES)
            self.copies[file_index] = (offset, len(block))
            self.records[(OWN, file_index)] = len(dol)
            dol += struct.pack('>12I', *([hh._DAT_FNAME_PTR, len(block), offset, len(block)] * 3))
        dol += b'\x00' * hh._ENTRY_SIZE
        self.output_dol.write_bytes(dol)
        self.output_dat.write_bytes(dat)
        for patcher in (
            mock.patch.object(hh, '_DIRS_COUNT', OWN),
            mock.patch.object(hh, '_extraDirStarts', return_value=[start]),
            mock.patch.object(hh, 'ownDirSource', side_effect=lambda chunk: SOURCE if chunk == OWN else None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_routes_of_an_own_directory(self):
        self.assertTrue(hh.isOwnDir(OWN))
        self.assertFalse(hh.isOwnDir(SOURCE))
        self.assertEqual(hh.readOutputDolEntry(OWN, 1), self.copies[1])
        self.assertEqual(hh.vanillaRoute(OWN, 1), (SOURCE, 1))
        self.assertEqual(hh.vanillaRoute(TARGET, 1), (TARGET, 1))

    def test_block_replaces_the_copy_and_leaves_every_vanilla_block(self):
        edited = model_block('kinopio_r.gpl', TOAD, fill=0x66)
        offset = self.write(edited, target=SlotTarget.Target(OWN, 0, 0x66))

        self.assertEqual(self.route((OWN, 0)), (offset, len(edited)))
        self.assertEqual(self.live((OWN, 0)), edited)
        self.assertEqual(self.bytes_at(*self.copies[0]), bytes(self.copies[0][1]))   # the old copy is freed
        self.assertEqual(self.route((OWN, 1)), self.copies[1])
        for route in VANILLA:
            self.assert_vanilla_route(route)       # nothing vanilla is zeroed: the directory has no vanilla range

    def test_skeleton_guard_uses_the_directory_source(self):
        with self.assertRaisesRegex(SlotTarget.TargetError, 'skeletons do not match'):
            self.write(VANILLA[(OTHER, 0)], source=(OTHER, 0), target=SlotTarget.Target(OWN, 0, 0x66))
        self.assertEqual(self.route((OWN, 0)), self.copies[0])

    def test_unpatch_is_refused(self):
        before = (self.output_dol.read_bytes(), self.output_dat.read_bytes())
        self.assertFalse(main.UnpatchTarget(SlotTarget.Target(OWN, 0, 0x66)))
        self.assertEqual(hh.removeModelFromHammerspace(OWN, 0), (False, 0, 0))
        self.assertEqual((self.output_dol.read_bytes(), self.output_dat.read_bytes()), before)


class ClearTargetTests(SlotHarness):
    """``--clear-target``: both of a stock slot's models back to vanilla."""

    def test_both_files_return_to_vanilla(self):
        self.write(model_block('kinopio_r.gpl', TOAD, fill=0x11), target=SlotTarget.Target(TARGET, 0))
        self.write(model_block('L_kinopio_r.gpl', TOAD, {3: 0}, fill=0x22), source=(SOURCE, 1),
                   target=SlotTarget.Target(TARGET, 1))
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)):
            self.assertTrue(main.ClearTarget('0x13'))
        self.assert_vanilla_route((TARGET, 0))
        self.assert_vanilla_route((TARGET, 1))

    def test_an_unchanged_slot_is_left_alone(self):
        before = self.output_dat.read_bytes()
        with mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)), \
                mock.patch.object(hh, 'removeModelFromHammerspace') as remove:
            self.assertTrue(main.ClearTarget('0x13'))
        remove.assert_not_called()
        self.assertEqual(self.output_dat.read_bytes(), before)


class SlotPairTests(unittest.TestCase):
    """``slot_pair_problems``, the checks a roster pack's blocks get before they go into a slot."""

    def problems(self, high, low, skeleton=TOAD):
        vanilla = {0: LodPartnerGuard.act_summary(model_block('kinopio_b.gpl', skeleton)),
                   1: LodPartnerGuard.act_summary(model_block('L_kinopio_b.gpl', skeleton))}
        return SlotTarget.slot_pair_problems(high, low, vanilla)

    def test_a_matching_pair_and_the_high_model_as_low(self):
        high, low = VANILLA[(SOURCE, 0)], VANILLA[(SOURCE, 1)]
        self.assertEqual(self.problems(high, low), ([], []))
        self.assertEqual(self.problems(high, high), ([], []))
        added = model_block('kinopio_r.gpl', TOAD + [3])                    # an added bone after the vanilla ones
        self.assertEqual(self.problems(added, low), ([], []))

    def test_another_characters_low_model_is_a_warning(self):
        errors, warnings = self.problems(VANILLA[(SOURCE, 0)], VANILLA[(TARGET, 1)])
        self.assertEqual(errors, [])
        self.assertIn('L_kinopio_b.gpl binds its textures', warnings[0])

    def test_errors(self):
        high, low = VANILLA[(SOURCE, 0)], VANILLA[(SOURCE, 1)]
        self.assertIn('skeletons do not match', self.problems(VANILLA[(OTHER, 0)], VANILLA[(OTHER, 1)])[0][0])
        self.assertIn('skeletons do not match', self.problems(model_block('kinopio_r.gpl', TOAD[:3]), low)[0][0])
        self.assertIn('is not a Low model', self.problems(high, model_block('kinopio_x.gpl', TOAD))[0][0])
        self.assertIn('is a Low model', self.problems(low, low)[0][0])
        draws_past = model_block('L_kinopio_r.gpl', TOAD + [3], {4: 0})
        self.assertIn('draws on bone(s) 4', self.problems(high, draws_past)[0][0])
        self.assertIn('no readable ACT', self.problems(b'\x00' * 0x40, low)[0][0])


class WriteSlotBlocksTests(SlotHarness):
    """``--write-slot-blocks`` writes a roster pack's finished blocks into a slot as they are."""

    def setUp(self):
        super().setUp()
        self.validate = self.enterContext(mock.patch('BlockValidator.validate_model_block',
                                                     return_value={'valid': True, 'errors': []}))
        self.enterContext(mock.patch.object(SlotTarget, 'resolve_dir', return_value=(0x13, TARGET)))

    def test_both_blocks_reach_the_slot(self):
        high, low = model_block('kinopio_r.gpl', TOAD, fill=0x11), model_block('L_kinopio_r.gpl', TOAD, {3: 0}, fill=0x22)
        self.assertTrue(main.WriteSlotBlocks('0x13', high, low))
        self.assertEqual((self.live((TARGET, 0)), self.live((TARGET, 1))), (high, low))
        self.assert_vanilla_route((SOURCE, 0))
        self.assert_vanilla_route((SOURCE, 1))

    def test_one_block_keeps_the_other_file(self):
        high = model_block('kinopio_b.gpl', TOAD, fill=0x33)
        self.assertTrue(main.WriteSlotBlocks('0x13', high, None))
        self.assertEqual(self.live((TARGET, 0)), high)
        self.assert_vanilla_route((TARGET, 1))

    def test_refusals_write_nothing(self):
        before = (self.output_dol.read_bytes(), self.output_dat.read_bytes())
        self.assertFalse(main.WriteSlotBlocks('0x13', VANILLA[(OTHER, 0)], VANILLA[(OTHER, 1)]))   # skeleton
        self.assertFalse(main.WriteSlotBlocks('0x13', None, model_block('L_kinopio_b.gpl', TOAD + [3], {4: 0})))
        self.validate.side_effect = lambda block: ({'valid': True, 'errors': []} if block in VANILLA.values()
                                                   else {'valid': False, 'errors': ['GPL broken']})
        self.assertFalse(main.WriteSlotBlocks('0x13', model_block('kinopio_b.gpl', TOAD, fill=0x44), None))
        self.assertEqual((self.output_dol.read_bytes(), self.output_dat.read_bytes()), before)

    def test_validator_errors_the_vanilla_block_has_too_are_tolerated(self):
        self.validate.return_value = {'valid': False, 'errors': ['TEX CLUT count 0 does not match 1 palette payload(s)']}
        high = model_block('kinopio_b.gpl', TOAD, fill=0x55)
        self.assertEqual(SlotTarget.block_errors(high, VANILLA[(TARGET, 0)]), [])
        self.assertEqual(SlotTarget.block_errors(high, None), ['TEX CLUT count 0 does not match 1 palette payload(s)'])
        self.assertTrue(main.WriteSlotBlocks('0x13', high, None))


class SlotDirectoryTests(unittest.TestCase):
    def test_stock_ids_load_their_own_directory(self):
        image, _ctx = build_roster({'ids': [{'id': '0x66', 'template': '0x06', 'wheel': '0x06'}]})
        self.assertEqual(slots.model_dir(image, 0x00), 0x12)
        self.assertEqual(slots.model_dir(image, 0x47), 89)

    def test_new_ids_without_own_directory_are_refused(self):
        image, _ctx = build_roster({'ids': [{'id': '0x66', 'template': '0x06', 'wheel': '0x06'}]})
        with self.assertRaisesRegex(slots.SlotError, 'template 0x06'):
            slots.model_dir(image, 0x66)
        with self.assertRaisesRegex(slots.SlotError, 'not a character'):
            slots.model_dir(image, 0x67)

    def test_parse_id(self):
        self.assertEqual(slots.parse_id('0x4A'), 0x4A)
        with self.assertRaises(slots.SlotError):
            slots.parse_id('Mario')


class PromoteInplaceEditsTests(unittest.TestCase):
    def test_inplace_uv_edit_gets_donor_faces_and_gpl_build(self):
        model = {'Submeshes': [{'UVChannels': [
            {'UVChannelData': 'AAE=', 'UVChannelDataEdited': 'AAI=', 'UVFacesData': 'AAA='}]}]}
        modes = main.PromoteInplaceEdits(model, main.SectionModes())
        self.assertEqual(modes.gpl, 'build')
        self.assertEqual(model['Submeshes'][0]['UVChannels'][0]['UVFacesDataEdited'], 'AAA=')

    def test_inline_skin_edit_needs_skn_build(self):
        model = {'Submeshes': [], 'SkinData': {'SK1s': [{'BindPoseDataEdited': 'AAA='}]}}
        self.assertEqual(main.PromoteInplaceEdits(model, main.SectionModes()).skn, 'build')

    def test_inplace_only_edits_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'facial'):
            main.PromoteInplaceEdits(
                {'FacialPoseDataEdited': {'Objects': [{'PositionPoseEdits': [{}]}]}}, main.SectionModes())
        with self.assertRaisesRegex(ValueError, 'vertex counts'):
            main.PromoteInplaceEdits({'SkinData': {'SK2s': [{'VertexCntEdited': 4}]}}, main.SectionModes())

    def test_hammerspace_files_are_left_alone(self):
        model = {'UseHammerspace': True, 'SkinData': {'SK2s': [{'VertexCntEdited': 4}]}}
        self.assertEqual(main.PromoteInplaceEdits(model, main.SectionModes()), main.SectionModes())


if __name__ == '__main__':
    unittest.main()
