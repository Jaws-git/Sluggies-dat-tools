"""``ImportDecode``, the importer's field decoders, imported directly: the module needs no Blender, so its
decoders run here on real field encodings and on damaged ones."""

import ast
import pathlib
import struct
import sys
import unittest

ADDON_DIR = pathlib.Path(__file__).resolve().parents[2] / 'BlenderAddonSrc'
if str(ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(ADDON_DIR))

import FieldCodec  # noqa: E402
import ImportDecode  # noqa: E402


class ModuleTests(unittest.TestCase):
    def test_no_blender_import(self):
        tree = ast.parse((ADDON_DIR / 'ImportDecode.py').read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
                imported.update(alias.name for alias in node.names)
        self.assertFalse(imported & {'bpy', 'bmesh', 'mathutils', 'bpy_extras'})
        # Its only add-on import is the field codec: none of the modules that need Blender.
        self.assertEqual(imported - {'struct', ''}, {'FieldCodec'})


class FacesTests(unittest.TestCase):
    def test_faces_round_trip(self):
        faces = [[0, 1, 2], [2, 1, 3]]
        data = FieldCodec.encode_field(struct.pack('>6H', *sum(faces, [])), True)
        self.assertEqual(ImportDecode.decode_faces({'FacesData': data, 'FacesCount': 2}), faces)

    def test_no_faces(self):
        self.assertEqual(ImportDecode.decode_faces({}), [])
        self.assertEqual(ImportDecode.decode_faces({'FacesData': '', 'FacesCount': 3}), [])

    def test_count_larger_than_the_data_is_an_error(self):
        data = FieldCodec.encode_field(struct.pack('>6H', 0, 1, 2, 2, 1, 3), True)
        with self.assertRaises(struct.error):
            ImportDecode.decode_faces({'FacesData': data, 'FacesCount': 3})


class VertexBufferTests(unittest.TestCase):
    def _buffer(self, raw, comp_count, quant):
        return {'VertexBufferData': FieldCodec.encode_field(raw, True),
                'VertexBufferQuantizeInfo': quant, 'VertexBufferCompCount': comp_count}

    def test_s16_positions(self):
        raw = struct.pack('>6h', 256, -256, 512, 0, 128, -128)
        positions, normals = ImportDecode.decode_vertex_buffer(self._buffer(raw, 3, 0x38))
        self.assertEqual(positions, [(1.0, -1.0, 2.0), (0.0, 0.5, -0.5)])
        self.assertEqual(normals, [])

    def test_float_positions_with_normals(self):
        raw = struct.pack('>6f', 1.0, 2.0, 3.0, 0.0, 1.0, 0.0)
        positions, normals = ImportDecode.decode_vertex_buffer(self._buffer(raw, 6, 0x40))
        self.assertEqual(positions, [(1.0, 2.0, 3.0)])
        self.assertEqual(normals, [(0.0, 1.0, 0.0)])

    def test_trailing_partial_record_is_left_out(self):
        raw = struct.pack('>6h', 256, 0, 0, 0, 256, 0) + b'\x01\x00\x00'
        positions, _normals = ImportDecode.decode_vertex_buffer(self._buffer(raw, 3, 0x38))
        self.assertEqual(len(positions), 2)

    def test_too_few_components_is_an_error(self):
        raw = struct.pack('>4h', 1, 2, 3, 4)
        with self.assertRaises(IndexError):
            ImportDecode.decode_vertex_buffer(self._buffer(raw, 2, 0x38))


if __name__ == '__main__':
    unittest.main()
