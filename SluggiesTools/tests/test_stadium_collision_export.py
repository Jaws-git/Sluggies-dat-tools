"""Add-on side of stadium collision (BlenderAddonSrc/StadiumCollisionExport.py),
checked against the patcher's reader of the field (Hammerspace/StadiumCollision.py)."""
import math
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON_DIR = ROOT / 'BlenderAddonSrc'
TOOLS_DIR = ROOT / 'SluggiesTools'
for path in (ADDON_DIR, TOOLS_DIR, TOOLS_DIR / 'Hammerspace'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import CustomSubmeshExport as cse  # noqa: E402
import StadiumCollision as sc  # noqa: E402
import StadiumCollisionExport as sce  # noqa: E402

# The importer's armature: +90 degrees about X (game y up -> Blender z up), so game
# (x, y, z) shows at Blender (x, -z, y).
ARM_WORLD = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]


def _normal(a, b, c):
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _blender_wall():
    """A quad at Blender y = 45 (game z = -45) whose normal points to Blender -y,
    i.e. towards home plate (game +z)."""
    cos = [(-40, 45, 0), (40, 45, 0), (40, 45, 25), (-40, 45, 25)]
    tris = [cse.Triangle((0, 1, 2), (0, 1, 2), 0), cse.Triangle((0, 2, 3), (0, 2, 3), 0)]
    assert _normal(*[cos[i] for i in (0, 1, 2)])[1] < 0
    return cos, tris


class MirrorTests(unittest.TestCase):
    def test_constants_match_the_patcher(self):
        self.assertEqual(sce.FIELD, sc.FIELD)
        self.assertEqual(sce.TOY_FIELD_CHUNK, sc.TOY_FIELD_DIR)
        self.assertEqual({v for _i, v, _l, _d in sce.SIDES}, set(sc.SIDES))
        self.assertEqual({v for _i, v, _l, _d in sce.TERRITORIES}, set(sc.TERRITORIES))
        self.assertEqual(sce.SURFACE_TYPES[0][:2], ('FENCE', sc.ATTR_FENCE))  # the in-play default
        self.assertEqual(sce.SURFACE_TYPE_NUMBERS['STANDS'], sc.ATTR_WALL)


class GeometryTests(unittest.TestCase):
    def test_model_space_and_front_side(self):
        cos, tris = _blender_wall()
        positions, faces = sce.model_space_geometry(cos, tris, cse.IDENTITY4, ARM_WORLD)
        for p in positions:
            self.assertAlmostEqual(p[2], -45.0)
            self.assertTrue(0 <= round(p[1], 6) <= 25)
        entry = sce.collision_entry('Wall', positions, faces, 2, 'FRONT', 'AUTO')
        triangles = sc.entry_triangles(entry)
        self.assertEqual({t[3] for t in triangles}, {2})
        for a, b, c, _attr in triangles:
            self.assertGreater(_normal(a, b, c)[2], 0)  # faces home plate

    def test_mirrored_object_keeps_its_front(self):
        cos, tris = _blender_wall()
        mirror_x = [[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        positions, faces = sce.model_space_geometry(cos, tris, mirror_x, ARM_WORLD)
        for face in faces:
            self.assertGreater(_normal(*[positions[i] for i in face])[2], 0)

    def test_moving_the_armature_cancels_out(self):
        cos, tris = _blender_wall()
        moved = [row[:] for row in ARM_WORLD]
        moved[0][3], moved[1][3] = 10.0, -3.0
        shifted = [(x + 10.0, y - 3.0, z) for x, y, z in cos]
        a, _ = sce.model_space_geometry(cos, tris, cse.IDENTITY4, ARM_WORLD)
        b, _ = sce.model_space_geometry(shifted, tris, cse.IDENTITY4, moved)
        for p, q in zip(a, b):
            for u, v in zip(p, q):
                self.assertAlmostEqual(u, v)


class EntryTests(unittest.TestCase):
    def test_entry_shape(self):
        entry = sce.collision_entry('Box', [(0, 0, 0), (1, 0, 0), (0, 1, 0), (9, 9, 9)],
                                    [(0, 1, 2)], 3, 'BOTH', 'FOUL')
        self.assertEqual(entry, {
            'Name': 'Box', 'SurfaceType': 3, 'Sides': 'both', 'Territory': 'foul',
            'Positions': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],  # unused vertex dropped
            'Faces': [[0, 1, 2]],
        })

    def test_degenerate_faces_dropped_and_empty_refused(self):
        with self.assertRaises(ValueError):
            sce.collision_entry('Flat', [(0, 0, 0), (0, 0, 0), (1, 0, 0)], [(0, 1, 2)], 2)
        with self.assertRaises(ValueError):
            sce.collision_entry('Nan', [(math.nan, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], 2)

    def test_surface_type_number(self):
        self.assertEqual(sce.surface_type_number('FENCE', 0), 2)
        self.assertEqual(sce.surface_type_number('CUSTOM', 13), 13)
        with self.assertRaises(ValueError):
            sce.surface_type_number('CUSTOM', 0)

    def test_chunk_errors(self):
        self.assertEqual(sce.chunk_error(18, []), '')
        self.assertIn('not a stadium', sce.chunk_error(18, ['Wall']))
        self.assertIn('Toy Field', sce.chunk_error(16, ['Wall']))
        self.assertEqual(sce.chunk_error(7, ['Wall']), '')

    def test_region_kept_on_original_colliders(self):
        entry = sce.collision_entry('col00_ground', [(0, 0, 0), (1, 0, 0), (0, 0, 1)], [(0, 1, 2)], 1,
                                    'FRONT', 'FAIR', region=3)
        self.assertEqual(entry['Region'], 3)
        self.assertNotIn('Region', sce.collision_entry('Wall', [(0, 0, 0), (1, 0, 0), (0, 0, 1)], [(0, 1, 2)], 2))

    def test_import_entries(self):
        own = [{'Name': 'Wall'}]
        vanilla = [{'Name': 'col00_ground', 'Region': 0}]
        self.assertEqual(sce.import_entries({sce.FIELD_VANILLA: vanilla, sce.FIELD: own}), vanilla + own)
        # an export that already rebuilt the collision holds the edited originals itself
        rebuilt = [{'Name': 'col00_ground', 'Region': 0}, {'Name': 'Wall'}]
        self.assertEqual(sce.import_entries({sce.FIELD_VANILLA: vanilla, sce.FIELD: rebuilt}), rebuilt)
        self.assertEqual(sce.import_entries({sce.FIELD_VANILLA: vanilla, sce.FIELD: own, sce.REPLACE_FIELD: True}), own)
        self.assertEqual((sce.FIELD_VANILLA, sce.REPLACE_FIELD), (sc.FIELD_VANILLA, sc.REPLACE_FIELD))

    def test_invisible_refused_on_original_meshes_only(self):
        self.assertEqual(sce.invisible_model_mesh_error([]), '')
        message = sce.invisible_model_mesh_error(['sm0'])
        self.assertIn('sm0', message)
        self.assertIn('Make invisible', message)


if __name__ == '__main__':
    unittest.main()
