"""RebuildCommon.py: the layer the custom submesh, rigid rebuild and body
rebuild exports share. The behaviour of what it holds is tested through the
three modules' own suites; this pins the layering and the names the old
homes keep exporting."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(ADDON_DIR))

import CustomSubmeshExport as cse  # noqa: E402
import HostBones  # noqa: E402
import RebuildCommon as rc  # noqa: E402
import RigidRebuildExport as rre  # noqa: E402
import SkinnedRebuildExport as sre  # noqa: E402


class LayeringTests(unittest.TestCase):
    def test_it_imports_none_of_the_three_modules_and_no_bpy(self):
        tree = ast.parse((ADDON_DIR / 'RebuildCommon.py').read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or '')
                imported.update(alias.name for alias in node.names)
        for name in ('bpy', 'CustomSubmeshExport', 'RigidRebuildExport', 'SkinnedRebuildExport'):
            self.assertNotIn(name, imported)

    def test_the_body_rebuild_no_longer_goes_through_the_rigid_module(self):
        self.assertFalse(hasattr(sre, 'rre'))

    def test_one_free_geo_id(self):
        self.assertIs(rc.GEO_ID_FREE, HostBones.GEO_ID_FREE)
        self.assertEqual(rre.GEO_ID_FREE, HostBones.GEO_ID_FREE)


class OldHomesTests(unittest.TestCase):
    """Names other modules and tests reach through the old modules."""

    def test_custom_submesh_export_names(self):
        for old, new in (
            ('int16_divisor', 'int16_divisor'), ('int16_range', 'int16_range'), ('_fits_int16', 'fits_int16'),
            ('quantize_int16', 'quantize_int16'), ('quantize_int16_lazy', 'quantize_int16_lazy'),
            ('position_format_fits', 'position_format_fits'), ('donor_position_candidates', 'donor_position_candidates'),
            ('widened_position_info', 'widened_position_info'), ('dedupe_records', 'dedupe_records'),
            ('_loop_order', 'loop_order'), ('is_unset_color', 'is_unset_color'),
            ('fill_unset_colors', 'fill_unset_colors'), ('encode_field', 'encode_field'),
            ('_index_buffer', 'index_buffer'), ('transform_normals', 'transform_normals'),
            ('normal_matrix', 'normal_matrix'), ('mat3_determinant', 'mat3_determinant'), ('WHITE', 'WHITE'),
        ):
            self.assertIs(getattr(cse, old), getattr(rc, new), old)
        self.assertEqual(cse.POSITION_QUANTIZE_CANDIDATES, tuple(range(59, 47, -1)))

    def test_rigid_rebuild_export_names(self):
        for name in (
            'SlotSurface', 'SurfaceRouting', 'new_surface_key_matches', 'route_faces', 'surface_report',
            'encode_color_entry', 'encode_color_channel', 'encode_uv_channels', 'facial_pose_submeshes',
            'donor_submesh_facts', 'FACIAL_KEY_NAME', 'FacialObjectKeys', 'facial_warning',
            'IN_PLACE_SUBMESH_FIELDS', 'IN_PLACE_CHANNEL_FIELDS', 'CarriedRebuilds', 'carry_unselected_rebuilds',
            'strip_in_place_edits', 'bone_geo_id_raw', 'REASON_TOPOLOGY', 'REASON_TRANSFORM', 'REASON_SURFACES',
            'REASON_POSITION_RANGE',
        ):
            self.assertIs(getattr(rre, name), getattr(rc, name), name)
        for name in ('CustomSubmeshSurfaces', 'route_custom_submesh_faces', 'custom_submesh_face_surface_indices'):
            self.assertIs(getattr(rre, name), getattr(cse, name), name)

    def test_skinned_rebuild_export_names(self):
        for name in ('FACIAL_KEY_NAME', 'FacialObjectKeys', 'facial_warning', 'strip_in_place_edits',
                     'REASON_TOPOLOGY', 'REASON_TRANSFORM', 'REASON_SURFACES', 'REASON_POSITION_RANGE'):
            self.assertIs(getattr(sre, name), getattr(rc, name), name)

    def test_both_donor_classes_are_the_shared_donor(self):
        self.assertTrue(issubclass(rre.DonorRigidSubmesh, rc.DonorSubmesh))
        self.assertTrue(issubclass(sre.DonorSkinnedSubmesh, rc.DonorSubmesh))
        shared = [f for f in rc.DonorSubmesh.__dataclass_fields__]
        self.assertEqual(list(rre.DonorRigidSubmesh.__dataclass_fields__), shared)
        self.assertEqual(list(sre.DonorSkinnedSubmesh.__dataclass_fields__), shared + ['skinned_bones'])


class SharedTextTests(unittest.TestCase):
    """The two texts that differ only by the name of the path."""

    def _donor(self, cls, **extra):
        return cls(index=1, position_quantize=0x40, normal_format=None, color_format=None, uv_formats={},
                   uv_mirrored=False, faces=[], face_surfaces=[], drawable_surfaces={}, facial=False, **extra)

    def test_position_format_error_names_its_path(self):
        rigid = rre.donor_position_format_error(self._donor(rre.DonorRigidSubmesh), 'Cap')
        body = sre.donor_position_format_error(self._donor(sre.DonorSkinnedSubmesh), 'Cap')
        self.assertIn('which the rigid rebuild does not support', rigid)
        self.assertIn('which the body rebuild does not support', body)
        self.assertEqual(rigid.replace('rigid rebuild', 'body rebuild'), body)

    def test_reason_order(self):
        self.assertEqual(
            rre.decide_reasons(topology_changed=True, moved=True, host_changed=True, surfaces_changed=True,
                               out_of_range=True),
            ['topology', 'host_bone_world', 'surfaces', 'position_range'])
        self.assertEqual(
            sre.decide_reasons(topology_changed=True, moved=True, surfaces_changed=True, weights_changed=True,
                               out_of_range=True),
            ['topology', 'object_transform', 'surfaces', 'skin_weights', 'position_range'])


if __name__ == '__main__':
    unittest.main()
