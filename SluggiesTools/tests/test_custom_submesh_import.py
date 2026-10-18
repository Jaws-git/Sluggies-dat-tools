"""Re-importing a .sluggie's own additions (custom submeshes, Add Bone bones).

A re-import used to drop both (user report, 2026-10-18). The decoding lives in the
bpy-free BlenderAddonSrc/CustomSubmeshImport.py; it is checked against the exporter's
encoder, so an import followed by an export gives the same entry back.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
for path in (BLENDER_ADDON_DIR, pathlib.Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import CustomSubmeshExport as cse  # noqa: E402
import CustomSubmeshImport as csi  # noqa: E402
from test_custom_submesh_export import (  # noqa: E402
    ARMATURE_WORLD, HOST_BIND, _cube_geometry, _cube_loop_attributes, _mul,
)

PLAN = cse.AttributePlan(normals=True, color=True, uv_channels=2, entry_limits={})
COLORS = [(1.0, 0.0, 0.0, 1.0) if loop < 18 else (0.0, 0.4, 1.0, 0.2) for loop in range(36)]


def _entry(geometry, normals, uvs, colors, plan=PLAN, **kwargs):
    return cse.build_custom_submesh_entry(
        'Cube', 'custom0', 12, 'builtin:rigid_spec_v1', plan,
        geometry, normals, uvs, colors, {'AdditionalTextureFileName': 'Cube.png'}, **kwargs,
    )


def _reexport(cs, plan=PLAN, **kwargs):
    """Export the decoded mesh again the way the add-on would after import:
    bone-local vertices at the host bind, one loop per corner in face order."""
    triangles = [cse.Triangle(face, tuple(3 * i + k for k in range(3)), i) for i, face in enumerate(cs.faces)]
    geometry = cse.bone_local_geometry(cs.positions, triangles, _mul(ARMATURE_WORLD, HOST_BIND),
                                       ARMATURE_WORLD, HOST_BIND)
    return _entry(geometry, cs.loop_normals, cs.loop_uvs, cs.loop_colors, plan, **kwargs)


class CustomSubmeshRoundTripTests(unittest.TestCase):
    def setUp(self):
        normals, uvs, _ = _cube_loop_attributes()
        self.entry = _entry(_cube_geometry(), normals, uvs, COLORS, specular_strength=80)
        self.cs = csi.decode_custom_submesh(self.entry)

    def test_identity_fields(self):
        self.assertEqual((self.cs.custom_submesh_id, self.cs.host_bone_id, self.cs.template_source),
                         ('custom0', 12, 'builtin:rigid_spec_v1'))
        self.assertEqual(self.cs.texture_assignment, {'AdditionalTextureFileName': 'Cube.png'})
        self.assertEqual(self.cs.specular_strength, 80)
        self.assertEqual(self.cs.problems, [])

    def test_geometry_is_bone_local(self):
        geometry = _cube_geometry()
        self.assertEqual(self.cs.faces, [tuple(f) for f in geometry.faces])
        for got, want in zip(self.cs.positions, geometry.positions):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, delta=1 / 2048)

    def test_every_corner_gets_its_attributes(self):
        self.assertEqual(len(self.cs.loop_uvs), 36)
        self.assertEqual(len(self.cs.loop_normals), 36)
        self.assertEqual(self.cs.loop_colors[0], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(self.cs.loop_uvs[:3], [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])

    def test_reexport_gives_the_same_entry(self):
        self.assertEqual(_reexport(self.cs, specular_strength=80), self.entry)

    def test_stadium_template_without_normals(self):
        plan = cse.AttributePlan(normals=False, color=True, uv_channels=1, entry_limits={})
        normals, uvs, _ = _cube_loop_attributes()
        entry = _entry(_cube_geometry(), normals, uvs, None, plan)
        cs = csi.decode_custom_submesh(entry)
        self.assertIsNone(cs.loop_normals)
        self.assertEqual(set(cs.loop_colors), {(1.0, 1.0, 1.0, 1.0)})
        self.assertEqual(_reexport(cs, plan), entry)

    def test_additional_surfaces_keep_their_faces(self):
        normals, uvs, _ = _cube_loop_attributes()
        surfaces = [{'SurfaceKey': 'custom0_new0', 'MaterialName': 'Cube_new0',
                     'TemplateSource': 'builtin:rigid_spec_v1',
                     'TextureAssignment': {'DonorTextureIndex': 3}}]
        indices = [0] * 6 + [1] * 6
        entry = _entry(_cube_geometry(), normals, uvs, None,
                       additional_surfaces=surfaces, face_surface_indices=indices)
        cs = csi.decode_custom_submesh(entry)
        self.assertEqual(cs.face_surfaces, indices)
        self.assertEqual(cs.additional_surfaces, surfaces)

    def test_a_short_index_buffer_drops_only_that_attribute(self):
        entry = dict(self.entry, ColorFacesData=cse.FieldCodec.encode_field(b'\x00\x00' * 5))
        cs = csi.decode_custom_submesh(entry)
        self.assertIsNone(cs.loop_colors)
        self.assertIsNotNone(cs.loop_uvs)
        self.assertIn('colours', cs.problems[0])

    def test_broken_faces_are_refused(self):
        with self.assertRaises(ValueError):
            csi.decode_custom_submesh(dict(self.entry, FacesCount=99))


class AddedBoneEntryTests(unittest.TestCase):
    def test_added_bones_in_id_order_with_known_parents(self):
        model = {
            'BoneHierarchy': [{'BoneId': 0}, {'BoneId': 1}],
            'BoneHierarchyEdited': [
                {'BoneId': 0, 'UserAdded': False}, {'BoneId': 1, 'UserAdded': False},
                {'BoneId': 3, 'UserAdded': True, 'ParentBoneId': 2},
                {'BoneId': 2, 'UserAdded': True, 'ParentBoneId': None},
                {'BoneId': 4, 'UserAdded': True, 'ParentBoneId': 9},  # unknown parent
            ],
        }
        self.assertEqual([b['BoneId'] for b in csi.added_bone_entries(model)], [2, 3])

    def test_no_edited_hierarchy(self):
        self.assertEqual(csi.added_bone_entries({'BoneHierarchy': [{'BoneId': 0}]}), [])


if __name__ == '__main__':
    unittest.main()
