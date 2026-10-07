import os
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

import LodPartnerGuard as guard
import LodTextureSync as sync

BIND_SETTING = 0x11110000  # layer 0 plus the wrap bits the vanilla binds carry


def _block(geo_name: str, submeshes: list[tuple[str, list[tuple[int, int]]]]) -> bytes:
    """A minimal model block: header, a GPL section whose submeshes hold only
    display states, then an ACT section carrying *geo_name*. Each submesh is
    ``(mesh name, [(slot, texture), ...])``; every pair becomes a Type-1 bind
    with params ``00 <slot> 08``, followed by a Type-7 draw state."""
    gpl = 0x20
    descriptors = 0x14
    body = bytearray(descriptors + len(submeshes) * 8)
    entries = []
    for name, binds in submeshes:
        base = len(body)
        display = 0x20
        states = display + 0x10
        blob = bytearray(states + len(binds) * 2 * 0x10)
        struct.pack_into('>I', blob, 0x10, display)
        struct.pack_into('>IIH', blob, display, 0, states, len(binds) * 2)
        for index, (slot, texture) in enumerate(binds):
            record = states + index * 0x20
            struct.pack_into('>B3sI', blob, record, 1, bytes((0, slot, 8)), BIND_SETTING | texture)
            struct.pack_into('>B3sI', blob, record + 0x10, 7, bytes((0x32, 0, 0x64)), 0x53706563)
        body += blob
        name_at = len(body)
        body += name.encode('ascii') + b'\x00'
        body += bytes(-len(body) % 4)
        entries.append((base, name_at))
    for index, (base, name_at) in enumerate(entries):
        struct.pack_into('>II', body, descriptors + index * 8, base, name_at)
    struct.pack_into('>IIIII', body, 0, sync._GPL_MAGIC, 0, 0, len(submeshes), descriptors)
    body += bytes(-len(body) % 0x20)

    act = gpl + len(body)
    table = struct.pack('>IIIIIHHBBH', 0, 0, 0, 0, 0, 0xFFFF, 0, 1, 0, 0)
    name_ptr = 0x20 + len(table)
    act_section = struct.pack('>IHHIIIHHII', 0, 0, 1, 0, 0x20, name_ptr, 0, 0, 0, 0) + table
    header = struct.pack('>8I', 0, gpl, act, 0, 0, 0, 0, 0)
    return header + bytes(body) + act_section + geo_name.encode('ascii') + b'\x00'


def _tiny(slots: dict[int, int], geo_name: str = 'tiny_kong.gpl', head=None) -> bytes:
    return _block(geo_name, [('body', sorted(slots.items())), ('head', head or [(7, 0)])])


VANILLA_BODY = {0: 0, 4: 0, 7: 0, 9: 0, 0x0A: 0}
HIGH_VANILLA = _tiny(VANILLA_BODY)
# Vanilla L_tiny_kong binds slot 0a to texture 2 where HP binds texture 0.
LOW_VANILLA = _tiny({**VANILLA_BODY, 0x0A: 2}, 'L_tiny_kong.gpl', head=[(0, 0)])
HIGH_EDITED = _tiny({0: 5, 4: 5, 7: 5, 9: 0, 0x0A: 0})


def _textures(block: bytes) -> dict[tuple[int, int], int]:
    return {(bind.submesh, bind.params[1]): bind.texture for bind in sync.texture_binds(block)}


class TextureBindsTests(unittest.TestCase):
    def test_reads_type1_binds_with_submesh_slot_and_texture(self):
        binds = sync.texture_binds(HIGH_EDITED)

        self.assertEqual([(b.submesh, b.mesh_name, b.params, b.texture) for b in binds[:2]],
                         [(0, 'body', bytes((0, 0, 8)), 5), (0, 'body', bytes((0, 4, 8)), 5)])
        self.assertEqual(struct.unpack_from('>I', HIGH_EDITED, binds[0].setting_offset)[0], BIND_SETTING | 5)

    def test_block_without_gpl_has_no_binds(self):
        self.assertEqual(sync.texture_binds(bytes(0x40)), [])


class PlanBindSyncTests(unittest.TestCase):
    def test_tiny_kong_retarget_is_mirrored_into_low_poly(self):
        # The follow rule: L binds that were in step with HP's vanilla index
        # take HP's new one; L's own slot-0a assignment (texture 2) is kept.
        plan = sync.plan_bind_sync(LOW_VANILLA, HIGH_EDITED, HIGH_VANILLA, HIGH_VANILLA, LOW_VANILLA)
        synced = _textures(sync.apply_edits(LOW_VANILLA, plan))

        self.assertEqual([synced[(0, slot)] for slot in (0, 4, 7)], [5, 5, 5])
        self.assertEqual(synced[(0, 0x0A)], 2)
        self.assertTrue(all(d.mirrored for d in plan.differences))
        self.assertEqual(len(plan.edits), 3)

    def test_mirroring_keeps_the_wrap_and_layer_bits(self):
        plan = sync.plan_bind_sync(LOW_VANILLA, HIGH_EDITED, HIGH_VANILLA, HIGH_VANILLA)

        self.assertTrue(all(setting == BIND_SETTING | 5 for _, setting in plan.edits))

    def test_vanilla_divergence_is_quiet(self):
        plan = sync.plan_bind_sync(LOW_VANILLA, HIGH_VANILLA, HIGH_VANILLA, HIGH_VANILLA, LOW_VANILLA)

        self.assertEqual((plan.edits, plan.differences), ([], []))

    def test_retargeted_slot_where_low_poly_already_diverged_is_kept_and_reported(self):
        high = _tiny({**VANILLA_BODY, 0x0A: 5})

        plan = sync.plan_bind_sync(LOW_VANILLA, high, HIGH_VANILLA, HIGH_VANILLA, LOW_VANILLA)

        self.assertEqual(plan.edits, [])
        [difference] = plan.differences
        self.assertFalse(difference.mirrored)
        self.assertEqual((difference.bind.params[1], difference.bind.texture, difference.high_texture), (0x0A, 2, 5))

    def test_low_poly_own_assignment_is_kept(self):
        low = _tiny({**VANILLA_BODY, 0: 3}, 'L_tiny_kong.gpl')

        plan = sync.plan_bind_sync(low, HIGH_EDITED, HIGH_VANILLA, HIGH_VANILLA)
        synced = _textures(sync.apply_edits(low, plan))

        self.assertEqual((synced[(0, 0)], synced[(0, 4)]), (3, 5))
        self.assertEqual([d.mirrored for d in plan.differences if d.bind.params[1] == 0], [False])

    def test_unpatching_high_poly_reverts_mirrored_binds(self):
        mirrored = sync.apply_edits(
            LOW_VANILLA, sync.plan_bind_sync(LOW_VANILLA, HIGH_EDITED, HIGH_VANILLA, HIGH_VANILLA),
        )

        plan = sync.plan_bind_sync(mirrored, HIGH_VANILLA, HIGH_EDITED, HIGH_VANILLA, LOW_VANILLA)

        self.assertEqual(sync.apply_edits(mirrored, plan), LOW_VANILLA)

    def test_reverting_returns_low_poly_to_its_own_vanilla_index(self):
        # HP slot 0a retargeted to 2 (already L's vanilla index), then unpatched.
        high = _tiny({**VANILLA_BODY, 0x0A: 2})

        plan = sync.plan_bind_sync(LOW_VANILLA, HIGH_VANILLA, high, HIGH_VANILLA, LOW_VANILLA)

        self.assertEqual(plan.edits, [])

    def test_repatching_follows_the_previous_high_poly_state(self):
        mirrored = sync.apply_edits(
            LOW_VANILLA, sync.plan_bind_sync(LOW_VANILLA, HIGH_EDITED, HIGH_VANILLA, HIGH_VANILLA),
        )
        high_again = _tiny({0: 6, 4: 5, 7: 5, 9: 0, 0x0A: 0})

        plan = sync.plan_bind_sync(mirrored, high_again, HIGH_EDITED, HIGH_VANILLA, LOW_VANILLA)

        self.assertEqual(_textures(sync.apply_edits(mirrored, plan))[(0, 0)], 6)

    def test_same_slot_in_another_submesh_is_a_different_bind(self):
        # HP sub1 'head' slot 07 retargeted; L's head has no slot 07, and its
        # body slot 07 must not follow the head.
        high = _tiny(VANILLA_BODY, head=[(7, 5)])

        plan = sync.plan_bind_sync(LOW_VANILLA, high, HIGH_VANILLA, HIGH_VANILLA, LOW_VANILLA)

        self.assertEqual((plan.edits, plan.differences), ([], []))

    def test_submeshes_with_different_names_are_not_matched(self):
        low = _block('L_tiny_kong.gpl', [('hair', sorted(VANILLA_BODY.items()))])

        plan = sync.plan_bind_sync(low, HIGH_EDITED, HIGH_VANILLA, HIGH_VANILLA)

        self.assertEqual(plan.edits, [])

    def test_custom_submesh_appended_to_high_poly_is_ignored(self):
        high = _block('tiny_kong.gpl', [('body', sorted(VANILLA_BODY.items())), ('head', [(7, 0)]),
                                        ('custom0', [(0, 5)])])

        plan = sync.plan_bind_sync(LOW_VANILLA, high, HIGH_VANILLA, HIGH_VANILLA, LOW_VANILLA)

        self.assertEqual((plan.edits, plan.differences), ([], []))

    def test_slot_bound_to_two_textures_in_one_submesh_is_skipped(self):
        low = _block('L_tiny_kong.gpl', [('body', [(0, 0), (0, 1)])])

        plan = sync.plan_bind_sync(low, HIGH_EDITED, HIGH_VANILLA, HIGH_VANILLA)

        self.assertEqual(plan.edits, [])


class AutoSyncDefaultTests(unittest.TestCase):
    def test_auto_sync_is_off(self):
        # The follow rule can pick the wrong surface; see the LodTextureSync
        # docstring.
        self.assertFalse(sync.AUTO_SYNC)


def _info_texts() -> list[str]:
    return [call.args[0] for call in sync._slogger.info.call_args_list]


class LiveSyncTests(unittest.TestCase):
    ROUTE = 0x100

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dat = os.path.join(self.tmp.name, 'dt_na.dat')
        with open(self.dat, 'wb') as dat:
            dat.write(bytes(self.ROUTE) + LOW_VANILLA + bytes(0x40))
        patches = [
            mock.patch.object(sync.hh, 'OUTPUT_DAT', self.dat),
            mock.patch.object(sync.hh, 'readOutputDolEntry', return_value=(self.ROUTE, len(LOW_VANILLA))),
            mock.patch.object(guard, '_entry_exists', return_value=True),
            mock.patch.object(sync, '_vanilla_block',
                              side_effect=lambda chunk, index: HIGH_VANILLA if index == 0 else LOW_VANILLA),
            mock.patch.object(sync._slogger, 'warning'),
            mock.patch.object(sync._slogger, 'info'),
            mock.patch.object(sync, 'AUTO_SYNC', True),
            mock.patch.object(sync.hh, 'liveRoutesInto', return_value=[(75, 1)]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def _live_low(self) -> bytes:
        with open(self.dat, 'rb') as dat:
            dat.seek(self.ROUTE)
            return dat.read(len(LOW_VANILLA))

    def test_patching_high_poly_writes_mirrored_binds_into_the_live_low_poly(self):
        with mock.patch.object(guard, 'read_current_block', return_value=LOW_VANILLA):
            plan = sync.sync_partner_of_high(75, 0, HIGH_EDITED, HIGH_VANILLA)

        self.assertEqual(len(plan.edits), 3)
        self.assertEqual([_textures(self._live_low())[(0, slot)] for slot in (0, 4, 7)], [5, 5, 5])
        sync._slogger.warning.assert_not_called()
        self.assertTrue(any('set L_tiny_kong.gpl to 5' in text for text in _info_texts()))

    def test_dry_run_logs_but_writes_nothing(self):
        with mock.patch.object(guard, 'read_current_block', return_value=LOW_VANILLA):
            sync.sync_partner_of_high(75, 0, HIGH_EDITED, HIGH_VANILLA, dry_run=True)

        self.assertEqual(self._live_low(), LOW_VANILLA)
        self.assertTrue(any('would set' in text for text in _info_texts()))

    def test_writing_a_low_poly_model_syncs_it_to_the_live_high_poly(self):
        with mock.patch.object(guard, 'read_current_block', return_value=HIGH_EDITED):
            synced = sync.sync_low_block(LOW_VANILLA, 75, 1)

        self.assertEqual([_textures(synced)[(0, slot)] for slot in (0, 4, 7)], [5, 5, 5])

    def test_high_poly_block_is_not_changed_by_the_low_poly_path(self):
        self.assertIs(sync.sync_low_block(HIGH_EDITED, 75, 0), HIGH_EDITED)

    def test_unpatched_low_poly_is_resynced_in_place(self):
        live = {0: HIGH_EDITED, 1: LOW_VANILLA}
        with mock.patch.object(guard, 'read_current_block', side_effect=lambda chunk, index: live[index]):
            sync.resync_live_low(75, 1)

        self.assertEqual(_textures(self._live_low())[(0, 0)], 5)

    def test_with_auto_sync_off_high_poly_patch_only_logs(self):
        with mock.patch.object(sync, 'AUTO_SYNC', False), \
                mock.patch.object(guard, 'read_current_block', return_value=LOW_VANILLA):
            plan = sync.sync_partner_of_high(75, 0, HIGH_EDITED, HIGH_VANILLA)

        self.assertEqual(len(plan.edits), 3)
        self.assertEqual(self._live_low(), LOW_VANILLA)
        sync._slogger.warning.assert_not_called()
        texts = _info_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn('not changed', texts[0])
        self.assertIn('cosmetic only', texts[0])

    def test_with_auto_sync_off_low_poly_block_is_written_unchanged(self):
        with mock.patch.object(sync, 'AUTO_SYNC', False), \
                mock.patch.object(guard, 'read_current_block', return_value=HIGH_EDITED):
            self.assertIs(sync.sync_low_block(LOW_VANILLA, 75, 1), LOW_VANILLA)
        sync._slogger.info.assert_called_once()
        sync._slogger.warning.assert_not_called()

    def test_with_auto_sync_off_unpatched_low_poly_is_not_resynced(self):
        live = {0: HIGH_EDITED, 1: LOW_VANILLA}
        with mock.patch.object(sync, 'AUTO_SYNC', False), \
                mock.patch.object(guard, 'read_current_block', side_effect=lambda chunk, index: live[index]):
            sync.resync_live_low(75, 1)
        self.assertEqual(self._live_low(), LOW_VANILLA)

    def test_low_poly_block_shared_with_another_route_is_not_written(self):
        # An unused character's L_ route re-tangled onto its owner's block
        # (UntanglePolicy): writing would change both models.
        live = {0: HIGH_EDITED, 1: LOW_VANILLA}
        with mock.patch.object(sync.hh, 'liveRoutesInto', return_value=[(75, 1), (89, 1)]):
            with mock.patch.object(guard, 'read_current_block', return_value=LOW_VANILLA):
                plan = sync.sync_partner_of_high(75, 0, HIGH_EDITED, HIGH_VANILLA)
            with mock.patch.object(guard, 'read_current_block', side_effect=lambda chunk, index: live[index]):
                sync.resync_live_low(75, 1)

        self.assertEqual(len(plan.edits), 3)
        self.assertEqual(self._live_low(), LOW_VANILLA)
        self.assertFalse(any('Synced' in text or 'Re-synced' in text for text in _info_texts()))
        self.assertIn('shared with route(s) (89,1)', sync._slogger.warning.call_args.args[0])

    def test_model_without_partner_is_left_alone(self):
        with mock.patch.object(guard, 'read_current_block', return_value=_tiny({}, 'luigi.gpl')):
            self.assertIsNone(sync.sync_partner_of_high(75, 0, HIGH_EDITED, HIGH_VANILLA))
        self.assertEqual(self._live_low(), LOW_VANILLA)


if __name__ == '__main__':
    unittest.main()
