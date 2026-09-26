"""Blender normal round-trip noise must not export as a normal edit.

Blender stores imported normals at lower precision than the donor's quantized
ints, so an untouched mesh re-quantizes a few steps off (user report
2026-09-25: Goomba sub0/sub1 exported with Overwrite Normals on produced
phantom normal edits on every loop, which sent the build down the GPL
tail-append path). AST-lift + exec tests for ExportSluggies.py -- no bpy
required.
"""

import ast
import base64
import math
import pathlib
import struct
import unittest

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
EXPORT_PATH = ROOT_DIR / 'BlenderAddonSrc' / 'ExportSluggies.py'

NAMES = {
    'math', 'struct', 'base64',
    '_to_bytes', '_from_bytes', '_pack_quantized_component',
    '_NORMAL_ROUNDTRIP_MIN_COS', '_decode_donor_components', '_normal_is_donor_roundtrip',
    'encode_vertex_buffer_edited', 'encode_normal_edits',
}


def _binds_name(node, names):
    if isinstance(node, ast.FunctionDef):
        return node.name in names
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((a.asname or a.name) in names for a in node.names)
    return False


def _extract(extra_globals):
    tree = ast.parse(EXPORT_PATH.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if _binds_name(node, NAMES)]
    namespace = dict(extra_globals)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(EXPORT_PATH), 'exec'), namespace)
    return namespace


class _Vec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __iter__(self):
        return iter((self.x, self.y, self.z))


def _unit(x, y, z):
    length = math.sqrt(x * x + y * y + z * z)
    return x / length, y / length, z / length


def _rotate_z(vector, degrees):
    x, y, z = vector
    a = math.radians(degrees)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a), z


class _Vertex:
    def __init__(self, index, co, normal):
        self.index = index
        self.co = _Vec(*co)
        self.normal = _Vec(*normal)


class _Mesh:
    def __init__(self, vertices):
        self.vertices = vertices
        self.shape_keys = None


class _Obj:
    name = 'sub'

    def __init__(self, mesh):
        self.data = mesh


SHIFT = 14
DIV = 1 << SHIFT
QI = (3 << 4) | SHIFT  # int16, 14 fractional bits
DONOR_NORMALS = [
    (6753, -3235, -14572),
    (7001, -2789, -14547),
    (0, 0, 0),  # unused/degenerate donor record
]


def _drifted(record, degrees=0.015):
    """Donor normal as Blender hands it back: float, slightly off, unit length."""
    if not any(record):
        return (0.0, 0.0, 1.0)  # Blender's substitute for a zero normal
    return _rotate_z(_unit(*(c / DIV for c in record)), degrees)


class VertexBufferNormalTests(unittest.TestCase):
    def _run(self, blender_normals, use_custom_normals):
        positions = [(0.5, 1.25, -1.5), (-1.0, 0.5, 0.25), (0.0, 0.0, 0.0)]
        donor = b''.join(
            struct.pack('>6h', *(round(c * DIV) for c in pos), *normal)
            for pos, normal in zip(positions, DONOR_NORMALS)
        )
        vertices = [
            _Vertex(i, pos, normal) for i, (pos, normal) in enumerate(zip(positions, blender_normals))
        ]
        custom = [_Vec(*n) for n in blender_normals]
        ns = _extract({'_get_custom_split_normals': lambda _obj: custom})
        out = ns['encode_vertex_buffer_edited'](
            _Obj(_Mesh(vertices)), 6, QI, use_custom_normals=use_custom_normals,
            use_base64=False, donor_data=list(donor),
        )
        return donor, bytes(out)

    def test_roundtrip_noise_keeps_donor_bytes(self):
        donor, out = self._run([_drifted(n) for n in DONOR_NORMALS], use_custom_normals=True)
        self.assertEqual(out, donor)

    def test_overwrite_off_keeps_donor_normals_even_when_blender_differs(self):
        edited = [_rotate_z(_drifted(n), 30.0) for n in DONOR_NORMALS]
        donor, out = self._run(edited, use_custom_normals=False)
        self.assertEqual(out, donor)

    def test_real_normal_edit_is_exported(self):
        normals = [_drifted(n) for n in DONOR_NORMALS]
        normals[0] = _rotate_z(normals[0], 5.0)
        donor, out = self._run(normals, use_custom_normals=True)
        self.assertNotEqual(out[:12], donor[:12])
        self.assertEqual(out[:6], donor[:6])  # position untouched
        self.assertEqual(out[12:], donor[12:])

    def test_vertex_count_change_falls_back_to_blender_normals(self):
        positions = [(0.5, 1.25, -1.5)]
        normal = _drifted(DONOR_NORMALS[0])
        ns = _extract({'_get_custom_split_normals': lambda _obj: [_Vec(*normal)]})
        donor = struct.pack('>6h', 0, 0, 0, *DONOR_NORMALS[0]) * 2  # two donor vertices
        out = bytes(ns['encode_vertex_buffer_edited'](
            _Obj(_Mesh([_Vertex(0, positions[0], normal)])), 6, QI,
            use_custom_normals=True, use_base64=False, donor_data=list(donor),
        ))
        expected = tuple(round(c * DIV) for c in normal)
        self.assertEqual(struct.unpack('>6h', out)[3:], expected)


class NormalBufferTests(unittest.TestCase):
    def _run(self, loop_normals, comp=3, donor_indices=(0, 1, 2, 1)):
        stride = comp * 2
        records = [
            struct.pack('>3h', *n) + (struct.pack('>3h', 7, 8, 9) if comp == 6 else b'')
            for n in DONOR_NORMALS
        ]
        buffer = {
            'NormalBufferCompCount': comp,
            'NormalBufferQuantizeInfo': QI,
            'NormalBufferData': base64.b64encode(b''.join(records)).decode(),
            'NormalFacesData': base64.b64encode(
                struct.pack(f'>{len(donor_indices)}H', *donor_indices)).decode(),
        }
        ns = _extract({'_per_loop_normals': lambda _mesh, loops: [loop_normals[i] for i in loops]})
        result = ns['encode_normal_edits'](
            _Obj(_Mesh([])), buffer, list(range(len(loop_normals))), use_base64=False,
        )
        expected = b''.join(records[i] for i in donor_indices)
        return result, expected, stride

    def test_roundtrip_noise_keeps_donor_records(self):
        loops = [_drifted(DONOR_NORMALS[i]) for i in (0, 1, 2, 1)]
        (data, _faces), expected, _ = self._run(loops)
        self.assertEqual(bytes(data), expected)

    def test_extended_records_keep_donor_tail(self):
        loops = [_drifted(DONOR_NORMALS[i]) for i in (0, 1, 2, 1)]
        loops[1] = _rotate_z(loops[1], 5.0)
        (data, _faces), expected, stride = self._run(loops, comp=6)
        data = bytes(data)
        self.assertEqual(data[:stride], expected[:stride])
        self.assertNotEqual(data[stride:stride + 6], expected[stride:stride + 6])
        self.assertEqual(data[stride + 6:2 * stride], struct.pack('>3h', 7, 8, 9))
        self.assertEqual(data[2 * stride:], expected[2 * stride:])

    def test_changed_loop_layout_quantizes_blender_normals(self):
        loops = [_drifted(DONOR_NORMALS[0])] * 5  # donor has 4 loops
        (data, _faces), _expected, _ = self._run(loops)
        expected = struct.pack('>3h', *(round(c * DIV) for c in loops[0])) * 5
        self.assertEqual(bytes(data), expected)


if __name__ == '__main__':
    unittest.main()
