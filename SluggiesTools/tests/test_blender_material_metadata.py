import ast
import pathlib
import struct
import unittest
from types import SimpleNamespace


ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
IMPORTER_PATH = ROOT_DIR / 'BlenderAddonSrc' / 'ImportSluggies.py'
EXPORTER_PATH = ROOT_DIR / 'BlenderAddonSrc' / 'ExportSluggies.py'


def _load_helper(path, name):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    )
    module = ast.Module(body=[helper], type_ignores=[])
    namespace = {}
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace[name]


def _load_metadata_helper():
    return _load_helper(IMPORTER_PATH, '_set_surface_material_metadata')


def _load_assignment_helpers():
    tree = ast.parse(EXPORTER_PATH.read_text(encoding='utf-8'))
    names = {
        '_effective_type7_modes',
        '_encode_face_surface_assignment',
    }
    helpers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(body=helpers, type_ignores=[])
    namespace = {
        'struct': struct,
        '_from_bytes': lambda value, _use_base64: list(value),
    }
    exec(compile(module, str(EXPORTER_PATH), 'exec'), namespace)
    return namespace


class _FakeUi:
    def __init__(self):
        self.values = {}

    def update(self, **values):
        self.values.update(values)


class _FakeMaterial(dict):
    def __init__(self):
        super().__init__()
        self.ui = {}

    def id_properties_ui(self, name):
        return self.ui.setdefault(name, _FakeUi())


class BlenderMaterialMetadataTests(unittest.TestCase):
    def test_type1_surface_exposes_raw_shader_mode(self):
        material = _FakeMaterial()

        _load_metadata_helper()(material, {
            'DisplayStateId': 1,
            'ShaderMode': '11110001',
        })

        self.assertEqual(material['DisplayStateId'], 1)
        self.assertEqual(material['ShaderMode'], '11110001')
        self.assertIn(
            'only Type-7 shader-mode edits are exported',
            material.ui['ShaderMode'].values['description'],
        )

    def test_type7_surface_exposes_editable_shader_mode(self):
        material = _FakeMaterial()

        _load_metadata_helper()(material, {
            'DisplayStateId': 7,
            'ShaderMode': 'Spec',
        })

        self.assertEqual(material['ShaderMode'], 'Spec')
        self.assertIn(
            'Editable Type-7 shader mode',
            material.ui['ShaderMode'].values['description'],
        )

    def test_type1_draw_batch_inherits_preceding_type7_mode(self):
        effective_modes = _load_helper(EXPORTER_PATH, '_effective_type7_modes')

        self.assertEqual(effective_modes([
            {'DisplayStateId': 1, 'ShaderMode': '11110000'},
            {'DisplayStateId': 7, 'ShaderMode': 'Spec'},
            {'DisplayStateId': 1, 'ShaderMode': '11110001'},
        ]), [None, 'Spec', 'Spec'])

    def test_export_accepts_type1_batch_moving_to_inherited_type7_surface(self):
        encode = _load_assignment_helpers()['_encode_face_surface_assignment']
        display_states = [
            {'SurfaceId': 'ds0', 'DisplayStateId': 1, 'ShaderMode': '11110000', 'FaceCount': 0},
            {'SurfaceId': 'ds1', 'DisplayStateId': 7, 'ShaderMode': 'Spec', 'FaceCount': 1},
            {'SurfaceId': 'ds2', 'DisplayStateId': 1, 'ShaderMode': '11110001', 'FaceCount': 1},
        ]
        material = {'SurfaceId': 'ds1'}
        obj = SimpleNamespace(
            name='mesh',
            material_slots=[SimpleNamespace(material=material)],
            data=SimpleNamespace(polygons=[
                SimpleNamespace(index=0, material_index=0),
                SimpleNamespace(index=1, material_index=0),
            ]),
        )

        encoded, changed = encode(obj, display_states, {}, False, [])

        self.assertTrue(changed)
        self.assertEqual(encoded, [0, 1, 0, 1])

    def test_export_accepts_type7_batch_moving_to_later_inheriting_type1_surface(self):
        encode = _load_assignment_helpers()['_encode_face_surface_assignment']
        display_states = [
            {'SurfaceId': 'ds0', 'DisplayStateId': 1, 'ShaderMode': '11110000', 'FaceCount': 0},
            {'SurfaceId': 'ds1', 'DisplayStateId': 7, 'ShaderMode': 'Spec', 'FaceCount': 1},
            {'SurfaceId': 'ds2', 'DisplayStateId': 1, 'ShaderMode': '11110001', 'FaceCount': 1},
        ]
        material = {'SurfaceId': 'ds2'}
        obj = SimpleNamespace(
            name='mesh',
            material_slots=[SimpleNamespace(material=material)],
            data=SimpleNamespace(polygons=[
                SimpleNamespace(index=0, material_index=0),
                SimpleNamespace(index=1, material_index=0),
            ]),
        )

        encoded, changed = encode(obj, display_states, {}, False, [])

        self.assertTrue(changed)
        self.assertEqual(encoded, [0, 2, 0, 2])

    def test_export_rejects_hand_role_to_spec_conversion(self):
        encode = _load_assignment_helpers()['_encode_face_surface_assignment']
        display_states = [
            {'SurfaceId': 'right', 'DisplayStateId': 7, 'ShaderMode': 'RhSp', 'FaceCount': 1},
            {'SurfaceId': 'body', 'DisplayStateId': 7, 'ShaderMode': 'Spec', 'FaceCount': 1},
        ]
        material = {'SurfaceId': 'body'}
        obj = SimpleNamespace(
            name='mesh',
            material_slots=[SimpleNamespace(material=material)],
            data=SimpleNamespace(polygons=[
                SimpleNamespace(index=0, material_index=0),
                SimpleNamespace(index=1, material_index=0),
            ]),
        )

        with self.assertRaisesRegex(ValueError, 'only identical effective Type-7'):
            encode(obj, display_states, {}, False, [])


if __name__ == '__main__':
    unittest.main()