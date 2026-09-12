import ast
import os
import pathlib
import struct
import tempfile
import unittest
from types import SimpleNamespace


ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
IMPORTER_PATH = ROOT_DIR / 'BlenderAddonSrc' / 'ImportSluggies.py'
EXPORTER_PATH = ROOT_DIR / 'BlenderAddonSrc' / 'ExportSluggies.py'
ADDON_INIT_PATH = ROOT_DIR / 'BlenderAddonSrc' / '__init__.py'


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


def _load_texture_helpers():
    tree = ast.parse(EXPORTER_PATH.read_text(encoding='utf-8'))
    names = {
        '_connected_image_texture_nodes',
        '_resolve_material_texture_changes',
        '_texture_export_toggles_required_message',
    }
    helpers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(body=helpers, type_ignores=[])
    namespace = {'os': os}
    exec(compile(module, str(EXPORTER_PATH), 'exec'), namespace)
    return namespace


class _FakeUi:
    def __init__(self):
        self.values = {}

    def update(self, **values):
        self.values.update(values)


class _FakeMaterial(dict):
    def __init__(self, name='material'):
        super().__init__()
        self.name = name
        self.ui = {}

    def id_properties_ui(self, name):
        return self.ui.setdefault(name, _FakeUi())


class _FakeInputs:
    def __init__(self, **sockets):
        self._sockets = sockets

    def __getitem__(self, name):
        return self._sockets[name]

    def __iter__(self):
        return iter(self._sockets.values())


def _node(node_type, **inputs):
    return SimpleNamespace(type=node_type, inputs=_FakeInputs(**inputs))


def _link(from_node):
    return SimpleNamespace(from_node=from_node)


def _socket(*nodes):
    return SimpleNamespace(links=[_link(node) for node in nodes])


def _material_graph(name, surface_id, texture_index, images, disconnected=()):
    image_nodes = [
        SimpleNamespace(
            type='TEX_IMAGE',
            inputs=_FakeInputs(),
            image=SimpleNamespace(filepath=str(image_path), filepath_raw=''),
        )
        for image_path in images
    ]
    shader = _node('BSDF_DIFFUSE', Color=_socket(*image_nodes))
    output = _node('OUTPUT_MATERIAL', Surface=_socket(shader))
    output.is_active_output = True
    material = _FakeMaterial(name)
    material.update(SurfaceId=surface_id, TextureIndex=texture_index)
    material.use_nodes = True
    material.node_tree = SimpleNamespace(
        nodes=[output, shader, *image_nodes, *disconnected]
    )
    return material


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


class BlenderMaterialTextureTests(unittest.TestCase):
    def setUp(self):
        helpers = _load_texture_helpers()
        self.connected_nodes = helpers['_connected_image_texture_nodes']
        self.resolve_changes = helpers['_resolve_material_texture_changes']
        self.toggle_message = helpers['_texture_export_toggles_required_message']

    @staticmethod
    def _object(*materials):
        return SimpleNamespace(
            material_slots=[SimpleNamespace(material=material) for material in materials]
        )

    def test_counts_only_image_nodes_connected_to_active_output(self):
        connected = SimpleNamespace(type='TEX_IMAGE', inputs=_FakeInputs(), image=None)
        disconnected = SimpleNamespace(type='TEX_IMAGE', inputs=_FakeInputs(), image=None)
        material = _material_graph('body', 'sm0_ds1', 0, [], (disconnected,))
        shader = material.node_tree.nodes[1]
        shader.inputs = _FakeInputs(Color=_socket(connected))
        material.node_tree.nodes.append(connected)

        self.assertEqual(self.connected_nodes(material), [connected])

    def test_zero_connected_images_is_unchanged(self):
        material = _material_graph('body', 'sm0_ds1', 0, [])
        obj = self._object(material)
        additions, assignments, changed = self.resolve_changes(
            [(obj, {})],
            [{'TextureIndex': 0, 'TextureFileName': '0.png'}],
            'unused',
        )
        self.assertEqual((additions, assignments, changed), ([], {}, []))

    def test_existing_donor_filename_is_rebind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pathlib.Path(temp_dir, '1.png').write_bytes(b'png')
            material = _material_graph(
                'body', 'sm0_ds1', 0, [pathlib.Path(temp_dir, '1.png')]
            )
            additions, assignments, changed = self.resolve_changes(
                [(self._object(material), {})],
                [
                    {'TextureIndex': 0, 'TextureFileName': '0.png'},
                    {'TextureIndex': 1, 'TextureFileName': '1.png'},
                ],
                temp_dir,
            )
        self.assertEqual(additions, [])
        self.assertEqual(assignments, {'sm0_ds1': 1})
        self.assertEqual(changed, ['body'])

    def test_original_donor_filename_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pathlib.Path(temp_dir, '0.png').write_bytes(b'png')
            material = _material_graph(
                'body', 'sm0_ds1', 0, [pathlib.Path(temp_dir, '0.png')]
            )
            result = self.resolve_changes(
                [(self._object(material), {})],
                [{'TextureIndex': 0, 'TextureFileName': '0.png'}],
                temp_dir,
            )
        self.assertEqual(result, ([], {}, []))

    def test_new_filename_appends_per_material_without_deduplication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = pathlib.Path(temp_dir, 'new.png')
            image_path.write_bytes(b'png')
            first = _material_graph('body', 'sm0_ds1', 0, [image_path])
            second = _material_graph('hand', 'sm0_ds2', 0, [image_path])
            additions, assignments, changed = self.resolve_changes(
                [(self._object(first, second), {})],
                [{'TextureIndex': 0, 'TextureFileName': '0.png'}],
                temp_dir,
            )
        self.assertEqual(additions, [
            {'TextureFileName': 'new.png', 'TemplateTextureIndex': 0},
            {'TextureFileName': 'new.png', 'TemplateTextureIndex': 0},
        ])
        self.assertEqual(assignments, {'sm0_ds1': 1, 'sm0_ds2': 2})
        self.assertEqual(changed, ['body', 'hand'])

    def test_missing_model_local_png_is_rejected(self):
        material = _material_graph('body', 'sm0_ds1', 0, ['elsewhere/new.png'])
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "must exist in this model's tex folder"):
                self.resolve_changes(
                    [(self._object(material), {})],
                    [{'TextureIndex': 0, 'TextureFileName': '0.png'}],
                    temp_dir,
                )

    def test_non_png_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = pathlib.Path(temp_dir, 'new.jpg')
            image_path.write_bytes(b'jpg')
            material = _material_graph('body', 'sm0_ds1', 0, [image_path])
            with self.assertRaisesRegex(ValueError, 'image must be a PNG'):
                self.resolve_changes(
                    [(self._object(material), {})],
                    [{'TextureIndex': 0, 'TextureFileName': '0.png'}],
                    temp_dir,
                )

    def test_multiple_connected_images_uses_exact_error(self):
        material = _material_graph(
            'body', 'sm0_ds1', 0, ['first.png', 'second.png']
        )
        with self.assertRaisesRegex(
            ValueError,
            '^Multiple textures in one material are not supported: body$',
        ):
            self.resolve_changes(
                [(self._object(material), {})],
                [{'TextureIndex': 0, 'TextureFileName': '0.png'}],
                'unused',
            )

    def test_texture_change_feedback_requires_both_toggles(self):
        self.assertEqual(
            self.toggle_message(['body', 'right hand']),
            "Texture change detected but 'Hammerspace Mode' and 'Reimport textures' "
            "are not both enabled. Enable both options before exporting. "
            "Materials: [body, right hand]",
        )


class BlenderExportUiTests(unittest.TestCase):
    def test_export_execute_rejects_empty_target_before_file_io(self):
        tree = ast.parse(EXPORTER_PATH.read_text(encoding='utf-8'))
        export_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'SLUGGIES_OT_export'
        )
        execute = next(
            node for node in export_class.body
            if isinstance(node, ast.FunctionDef) and node.name == 'execute'
        )
        first_statement = execute.body[0]
        self.assertIsInstance(first_statement, ast.If)
        source = ast.unparse(first_statement)
        self.assertIn('No target .sluggie file was selected', source)
        self.assertIn("return {'CANCELLED'}", source)

    def test_sidebar_module_is_retained_but_not_registered(self):
        self.assertTrue((ROOT_DIR / 'BlenderAddonSrc' / 'SluggiesToolsPanel.py').is_file())
        tree = ast.parse(ADDON_INIT_PATH.read_text(encoding='utf-8'))
        imported_modules = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertNotIn('SluggiesToolsPanel', imported_modules)
        self.assertNotIn(
            'SluggiesToolsPanel.register',
            ADDON_INIT_PATH.read_text(encoding='utf-8'),
        )


if __name__ == '__main__':
    unittest.main()