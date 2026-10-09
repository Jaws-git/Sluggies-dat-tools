"""Triangle strips in drawlist.encodeDrawList (PLAN_ModelReplacements.md
4.1): the greedy stripifier and its round trip through decodeDrawList."""
import pathlib
import random
import struct
import sys
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import drawlist  # noqa: E402

DESCRIPTORS = [
    {'key': 'position', 'direct': False, 'index_size': 2},
    {'key': 'texture0', 'direct': False, 'index_size': 1},
]


def _rotated(tri):
    k = min(range(3), key=lambda i: tri[i])
    return (tri[k], tri[(k + 1) % 3], tri[(k + 2) % 3])


def _faces(triangles):
    """Vertex-dict faces (position = corner, texture0 = corner % 7)."""
    return [[{'position': c, 'texture0': c % 7} for c in tri] for tri in triangles]


def _grid(width: int, height: int):
    """A consistently wound triangle grid of (width x height) quads."""
    tris = []
    for y in range(height):
        for x in range(width):
            a = y * (width + 1) + x
            b, c, d = a + 1, a + width + 1, a + width + 2
            tris += [(a, b, c), (b, d, c)]
    return tris


def _roundtrip(self, triangles, strips=True):
    raw = drawlist.encodeDrawList(_faces(triangles), DESCRIPTORS, strips=strips)
    back = drawlist.decodeDrawList(raw, DESCRIPTORS)
    got = sorted(_rotated(tuple(v['position'] for v in face)) for face in back)
    self.assertEqual(got, sorted(_rotated(tuple(t)) for t in triangles))
    for face in back:                       # the other attributes travel with their corner
        for vertex in face:
            self.assertEqual(vertex['texture0'], vertex['position'] % 7)
    return raw


def _blocks(raw: bytes):
    """``[(primitive, vertex_count)]`` of an encoded list."""
    stride = sum(d['index_size'] for d in DESCRIPTORS)
    out, pos = [], 0
    while pos < len(raw) and raw[pos]:
        count = struct.unpack_from('>H', raw, pos + 1)[0]
        out.append((raw[pos], count))
        pos += 3 + count * stride
    return out


class StripifyTests(unittest.TestCase):
    def test_grid_strips_to_few_long_runs(self):
        tris = _grid(10, 10)
        strips, leftovers = drawlist.stripify(tris)
        self.assertEqual(sum(len(s) - 2 for s in strips) + len(leftovers), len(tris))
        self.assertLess(len(strips), 25)
        self.assertLess(len(leftovers), 10)

    def test_fan_strips_into_short_runs(self):
        # A strip zig-zags, so a fan around one vertex only gives short runs
        # (the centre can sit in the middle of a run: [9, 8, 0, 7, 6] is
        # three fan triangles); nothing is lost and at most one stays plain.
        tris = [(0, k, k + 1) for k in range(1, 9)]
        strips, leftovers = drawlist.stripify(tris)
        self.assertEqual(sum(len(s) - 2 for s in strips) + len(leftovers), 8)
        self.assertTrue(all(len(s) >= 4 for s in strips))
        self.assertLessEqual(len(leftovers), 1)

    def test_degenerate_and_isolated_triangles_stay_plain(self):
        tris = [(0, 0, 1), (5, 6, 7), (0, 1, 2), (0, 2, 3)]
        strips, leftovers = drawlist.stripify(tris)
        self.assertEqual(sorted(leftovers), [(0, 0, 1), (5, 6, 7)])
        self.assertEqual(len(strips), 1)

    def test_folded_edge_is_not_walked(self):
        # Two triangles with the same directed edge (0, 1): opposite sides
        # of a fold. Neither may continue through it.
        tris = [(0, 1, 2), (0, 1, 3)]
        strips, leftovers = drawlist.stripify(tris)
        self.assertEqual(strips, [])
        self.assertEqual(sorted(leftovers), sorted(tris))

    def test_decoder_reads_every_strip_back(self):
        rng = random.Random(7)
        tris = _grid(6, 4)
        rng.shuffle(tris)
        strips, leftovers = drawlist.stripify(tris)
        decoded = []
        for s in strips:
            for i in range(len(s) - 2):
                decoded.append((s[i + 2], s[i + 1], s[i]) if i % 2 == 0 else (s[i], s[i + 1], s[i + 2]))
        decoded += leftovers
        self.assertEqual(sorted(map(_rotated, decoded)), sorted(map(_rotated, tris)))


class EncodeStripsTests(unittest.TestCase):
    def test_strips_round_trip_and_halve_the_bytes(self):
        tris = _grid(12, 12)
        plain = _roundtrip(self, tris, strips=False)
        stripped = _roundtrip(self, tris, strips=True)
        self.assertLess(len(stripped), 0.6 * len(plain))
        kinds = {primitive for primitive, _count in _blocks(stripped)}
        self.assertIn(drawlist.GX_TRIANGLESTRIP, kinds)

    def test_leftovers_share_one_triangles_block(self):
        tris = [(0, 1, 2), (10, 11, 12), (20, 21, 22), (30, 31, 32)]
        raw = _roundtrip(self, tris)
        self.assertEqual(_blocks(raw), [(drawlist.GX_TRIANGLES, 12)])

    def test_two_triangle_strip_is_kept(self):
        raw = _roundtrip(self, [(0, 1, 2), (0, 2, 3)])
        self.assertEqual(_blocks(raw), [(drawlist.GX_TRIANGLESTRIP, 4)])

    def test_shuffled_and_mirrored_inputs(self):
        rng = random.Random(3)
        tris = _grid(5, 7)
        rng.shuffle(tris)
        _roundtrip(self, tris)
        _roundtrip(self, [(c, b, a) for a, b, c in tris])

    def test_corner_identity_includes_every_drawn_attribute(self):
        # Same positions, different texture indices: not the same GX vertex,
        # so the two triangles cannot share a strip edge.
        faces = [
            [{'position': 0, 'texture0': 0}, {'position': 1, 'texture0': 0}, {'position': 2, 'texture0': 0}],
            [{'position': 0, 'texture0': 1}, {'position': 2, 'texture0': 1}, {'position': 3, 'texture0': 1}],
        ]
        raw = drawlist.encodeDrawList(faces, DESCRIPTORS, strips=True)
        self.assertEqual(_blocks(raw), [(drawlist.GX_TRIANGLES, 6)])
        back = drawlist.decodeDrawList(raw, DESCRIPTORS)
        self.assertEqual(sorted(sorted((v['position'], v['texture0']) for v in f) for f in back),
                         sorted(sorted((v['position'], v['texture0']) for v in f) for f in faces))

    def test_empty_and_default_mode_unchanged(self):
        self.assertEqual(drawlist.encodeDrawList([], DESCRIPTORS, strips=True), b'')
        tris = _grid(2, 2)
        self.assertEqual(drawlist.encodeDrawList(_faces(tris), DESCRIPTORS),
                         drawlist.encodeDrawList(_faces(tris), DESCRIPTORS, strips=False))
        self.assertEqual(_blocks(drawlist.encodeDrawList(_faces(tris), DESCRIPTORS)), [(drawlist.GX_TRIANGLES, 24)])


if __name__ == '__main__':
    unittest.main()
