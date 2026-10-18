"""Stadium collision files (``Hammerspace/StadiumCollision.py``), synthetic half.

The round trip over every vanilla collision file lives in the hand-run probe
``SluggiesTools/Debug/stadium_collision_probe.py check`` (the game assets are
gitignored).
"""
from __future__ import annotations

import os
import struct
import sys
import unittest

TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
HAMMERSPACE_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import StadiumCollision as sc  # noqa: E402


def _vanilla_like() -> bytes:
    """Two regions, a strip and a list, packed like the game's files."""
    strip = [(0.0, 0.0, 0.0, 1), (1.0, 0.0, 0.0, 1), (0.0, 0.0, 1.0, 1), (1.0, 0.0, 1.0, 0x81)]
    lst = [(5.0, -2.0, 3.0, 0), (6.0, -2.0, 3.0, 0), (5.0, -4.0, 3.0, 3)]
    regions = [[sc.Strip(1, 2, strip)], [sc.Strip(0, 1, lst)]]
    boxes = [sc.region_box(r) for r in regions]
    return sc.serialize(boxes, regions)


def _normal(a, b, c):
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


class FormatTests(unittest.TestCase):
    def test_layout_is_packed(self):
        data = _vanilla_like()
        n, version, relocated, *offsets = struct.unpack_from('>HBB3I', data, 0)
        self.assertEqual((n, version, relocated), (2, 0x43, 0))
        self.assertEqual(offsets[0], 16)                    # boxes right after the header
        self.assertEqual(offsets[1], 16 + 2 * 24)           # region 1 right after the boxes
        self.assertEqual(offsets[2], offsets[1] + 4 + 4 * 16 + 4)
        self.assertEqual(len(data), offsets[2] + 4 + 3 * 16 + 4)

    def test_round_trip(self):
        data = _vanilla_like()
        self.assertEqual(sc.serialize(*sc.parse(data)), data)

    def test_refuses_other_files(self):
        with self.assertRaises(ValueError):
            sc.parse(b'\x00\x01\x42\x00' + b'\x00' * 12)

    def test_bad_strip_refused(self):
        with self.assertRaises(ValueError):
            sc.serialize([(0,) * 6], [[sc.Strip(1, 3, [(0, 0, 0, 1)] * 4)]])


class AddRegionTests(unittest.TestCase):
    def test_vanilla_regions_unchanged(self):
        data = _vanilla_like()
        wall = sc.quad((-1, 0, -5), (1, 0, -5), (1, 2, -5), (-1, 2, -5), sc.ATTR_WALL)
        out = sc.add_regions(data, [wall])
        boxes, regions = sc.parse(out)
        old_boxes, old_regions = sc.parse(data)
        self.assertEqual(boxes[:2], old_boxes)
        self.assertEqual(regions[:2], old_regions)
        self.assertEqual(len(regions), 3)

    def test_new_region_in_file_space_with_box(self):
        wall = sc.quad((-1, 0, -5), (1, 0, -5), (1, 2, -5), (-1, 2, -5), sc.ATTR_WALL)
        boxes, regions = sc.parse(sc.add_regions(_vanilla_like(), [wall]))
        (strip,) = regions[2]
        self.assertEqual((strip.kind, strip.count), (0, 2))
        self.assertEqual(strip.verts[0], (-1.0, -0.0, 5.0, 3))  # (x, -y, -z)
        self.assertEqual({v[3] for v in strip.verts}, {3})
        self.assertEqual(boxes[2], (-1.0, -2.0, 5.0, 1.0, 0.0, 5.0))

    def test_winding_kept(self):
        # quad a-b-c-d counter-clockwise seen from +z: the normal faces +z (home plate)
        wall = sc.quad((-1, 0, -5), (1, 0, -5), (1, 2, -5), (-1, 2, -5), sc.ATTR_WALL)
        for a, b, c, _attr in wall:
            self.assertGreater(_normal(a, b, c)[2], 0)
        _, regions = sc.parse(sc.add_regions(_vanilla_like(), [wall]))
        verts = regions[2][0].verts
        game = [(x, -y, -z) for x, y, z, _ in verts]
        self.assertGreater(_normal(*game[:3])[2], 0)
        # a rotation (y, z negated) keeps the winding: same side in file space
        self.assertLess(_normal(*[v[:3] for v in verts[:3]])[2], 0)

    def test_strip_order_comes_back_as_a_strip(self):
        data = _vanilla_like()
        _, regions = sc.parse(data)
        tris = list(sc.triangles_game(regions[0]))
        _, strips = sc.region_from_triangles(tris)
        self.assertEqual(strips, regions[0])

    def test_large_region_split_into_lists(self):
        tri = ((0, 0, 0), (1, 0, 0), (0, 1, 0), 1)
        _, strips = sc.region_from_triangles([tri] * (sc.MAX_LIST_COUNT + 2))
        self.assertEqual([s.count for s in strips], [sc.MAX_LIST_COUNT, 2])

    def test_empty_region_refused(self):
        with self.assertRaises(ValueError):
            sc.region_from_triangles([])


def _wall_entry(**overrides):
    entry = {
        'Name': 'Wall', 'SurfaceType': 2, 'Sides': 'front', 'Territory': 'auto',
        'Positions': [[-1, 0, -5], [1, 0, -5], [1, 2, -5], [-1, 2, -5]],
        'Faces': [[0, 1, 2], [0, 2, 3]],
    }
    entry.update(overrides)
    return entry


class SluggieEntryTests(unittest.TestCase):
    def test_front_keeps_winding_and_type(self):
        tris = sc.entry_triangles(_wall_entry())
        self.assertEqual(len(tris), 2)
        self.assertEqual({t[3] for t in tris}, {2})
        for a, b, c, _attr in tris:
            self.assertGreater(_normal(a, b, c)[2], 0)

    def test_both_sides_adds_reversed_faces(self):
        tris = sc.entry_triangles(_wall_entry(Sides='both'))
        self.assertEqual(len(tris), 4)
        self.assertEqual(sorted(_normal(a, b, c)[2] > 0 for a, b, c, _ in tris), [False, False, True, True])

    def test_auto_territory_follows_foul_lines(self):
        fair = ((-1, 0, -10), (1, 0, -10), (0, 1, -10))
        foul = ((20, 0, -10), (22, 0, -10), (21, 1, -10))
        behind_home = ((-1, 0, 5), (1, 0, 5), (0, 1, 5))
        self.assertFalse(sc.in_foul_territory(*fair))
        self.assertTrue(sc.in_foul_territory(*foul))
        self.assertTrue(sc.in_foul_territory(*behind_home))
        entry = _wall_entry(Positions=[list(v) for v in fair + foul], Faces=[[0, 1, 2], [3, 4, 5]])
        self.assertEqual([t[3] for t in sc.entry_triangles(entry)], [2, 0x82])

    def test_fixed_territory(self):
        self.assertEqual({t[3] for t in sc.entry_triangles(_wall_entry(Territory='foul'))}, {0x82})
        far_foul = _wall_entry(Positions=[[50, 0, -5], [52, 0, -5], [52, 2, -5], [50, 2, -5]], Territory='fair')
        self.assertEqual({t[3] for t in sc.entry_triangles(far_foul)}, {2})

    def test_bad_entries_refused(self):
        for bad in (dict(SurfaceType=0), dict(SurfaceType=0x80), dict(Sides='back'),
                    dict(Territory='x'), dict(Faces=[[0, 1, 9]]), dict(Faces=[]),
                    dict(Positions=[[0, 0]] * 4)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sc.entry_triangles(_wall_entry(**bad))

    def test_vanilla_entries_rebuild_the_same_triangles(self):
        data = _vanilla_like()
        entries = sc.vanilla_entries(data)
        self.assertEqual([(e['Region'], e['SurfaceType'], e['Territory']) for e in entries],
                         [(0, 1, 'fair'), (0, 1, 'foul'), (1, 3, 'fair')])
        self.assertTrue(all(e['Sides'] == 'front' for e in entries))

        def tris(blob):
            return [sorted(sc.triangles_game(s)) for s in sc.parse(blob)[1]]
        self.assertEqual(tris(sc.build_from_entries(entries)), tris(data))

    def test_replace_mode_drops_missing_regions_and_appends_new(self):
        entries = sc.vanilla_entries(_vanilla_like())
        kept = [e for e in entries if e['Region'] == 1] + [_wall_entry()]
        _, regions = sc.parse(sc.build_from_entries(kept))
        self.assertEqual(len(regions), 2)  # region 1, then the new wall
        self.assertEqual({t[3] for t in sc.triangles_game(regions[0])}, {3})
        self.assertEqual({t[3] for t in sc.triangles_game(regions[1])}, {2})
        self.assertTrue(sc.is_replace(kept))
        self.assertFalse(sc.is_replace([_wall_entry()]))

    def test_all_colliders_removed_is_refused(self):
        model = {'ChunkNumber': 10, 'FileIndex': 0, 'StadiumCollision': [], sc.REPLACE_FIELD: True}
        with self.assertRaisesRegex(ValueError, 'fall through'):
            sc.apply_model(model, dry_run=True)

    def test_no_field_does_nothing(self):
        self.assertEqual(sc.apply_model({'ChunkNumber': 10, 'FileIndex': 0}), [])


if __name__ == '__main__':
    unittest.main()
