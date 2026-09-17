import ast
import os
import pathlib
import tempfile
import unittest

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
PANEL_PATH = ROOT_DIR / 'BlenderAddonSrc' / 'SluggiesToolsPanel.py'


def _binds_name(node, names):
    if isinstance(node, ast.FunctionDef):
        return node.name in names
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((a.asname or a.name) in names for a in node.names)
    return False


def _extract(names):
    """Load the given top-level names (imports/assignments/functions) from
    SluggiesToolsPanel.py into an executable module, in source order, without
    importing bpy/bmesh."""
    tree = ast.parse(PANEL_PATH.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if _binds_name(node, names)]
    module = ast.Module(body=nodes, type_ignores=[])
    namespace = {}
    exec(compile(module, str(PANEL_PATH), 'exec'), namespace)
    return namespace


class _FakeObj:
    def __init__(self, **props):
        self._props = props

    def get(self, key, default=None):
        return self._props.get(key, default)

    def keys(self):
        return self._props.keys()


class BoneIdFromNameTests(unittest.TestCase):
    def setUp(self):
        ns = _extract({'re', '_BONE_NAME_RE', '_bone_id_from_name'})
        self.fn = ns['_bone_id_from_name']

    def test_parses_bone_id(self):
        self.assertEqual(self.fn('bone_42'), 42)

    def test_rejects_non_matching_name(self):
        self.assertIsNone(self.fn('armature'))

    def test_rejects_none(self):
        self.assertIsNone(self.fn(None))


class NextCustomSubmeshIdTests(unittest.TestCase):
    def setUp(self):
        ns = _extract({'_next_custom_submesh_id'})
        self.fn = ns['_next_custom_submesh_id']

    def _context(self, objects):
        class _ViewLayer:
            pass
        vl = _ViewLayer()
        vl.objects = objects

        class _Context:
            pass
        ctx = _Context()
        ctx.view_layer = vl
        return ctx

    def test_first_id_when_scene_is_empty(self):
        self.assertEqual(self.fn(self._context([])), 'custom0')

    def test_skips_ids_already_used(self):
        objs = [_FakeObj(CustomSubmeshId='custom0'), _FakeObj(CustomSubmeshId='custom1')]
        self.assertEqual(self.fn(self._context(objs)), 'custom2')

    def test_ignores_objects_without_the_property(self):
        objs = [_FakeObj(), _FakeObj(CustomSubmeshId='custom0')]
        self.assertEqual(self.fn(self._context(objs)), 'custom1')


class WriteCustomSubmeshTextureTests(unittest.TestCase):
    def setUp(self):
        ns = _extract({'os', '_write_custom_submesh_texture', '_CUSTOM_SUBMESH_TEXTURE_SIZE'})
        self.fn = ns['_write_custom_submesh_texture']

    def test_refuses_to_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            sluggie_path = os.path.join(tmp, 'model.sluggie')
            tex_dir = os.path.join(tmp, 'tex')
            os.makedirs(tex_dir)
            existing = os.path.join(tex_dir, 'CustomSubmesh_0.png')
            with open(existing, 'wb') as f:
                f.write(b'not really a png')

            class _ExplodingImages:
                def new(self, *args, **kwargs):
                    raise AssertionError('bpy.data.images.new must not run when the file already exists')

            class _ExplodingData:
                images = _ExplodingImages()

            class _ExplodingBpy:
                data = _ExplodingData()

            self.fn.__globals__['bpy'] = _ExplodingBpy()
            image, error = self.fn(sluggie_path, 'CustomSubmesh_0')
            self.assertIsNone(image)
            self.assertIn('already exists', error)

    def test_creates_directory_and_saves_new_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            sluggie_path = os.path.join(tmp, 'model.sluggie')
            calls = {}

            class _FakeImage:
                def save(self):
                    calls['saved'] = True

            class _FakeImages:
                def new(self, name, width, height, alpha):
                    calls['new'] = (name, width, height, alpha)
                    return _FakeImage()

            class _FakeData:
                images = _FakeImages()

            class _FakeBpy:
                data = _FakeData()

            self.fn.__globals__['bpy'] = _FakeBpy()
            image, error = self.fn(sluggie_path, 'CustomSubmesh_0')
            self.assertIsNone(error)
            self.assertIsNotNone(image)
            self.assertTrue(calls.get('saved'))
            self.assertEqual(calls['new'][0], 'CustomSubmesh_0.png')
            self.assertTrue(os.path.isdir(os.path.join(tmp, 'tex')))


class DefaultSubmeshNameTests(unittest.TestCase):
    def setUp(self):
        ns = _extract({'re', '_SUBMESH_NAME_RE', '_default_submesh_name'})
        self.fn = ns['_default_submesh_name']

    def _context(self, names):
        class _ViewLayer:
            pass
        vl = _ViewLayer()
        vl.objects = [_FakeNamedObj(n) for n in names]

        class _Context:
            pass
        ctx = _Context()
        ctx.view_layer = vl
        return ctx

    def test_zero_when_scene_is_empty(self):
        self.assertEqual(self.fn(self._context([])), 'CustomSubmesh_0')

    def test_skips_taken_numbers(self):
        self.assertEqual(
            self.fn(self._context(['CustomSubmesh_0', 'CustomSubmesh_1'])),
            'CustomSubmesh_2')

    def test_ignores_unrelated_object_names(self):
        self.assertEqual(
            self.fn(self._context(['bone_helper', 'CustomSubmesh_0', 'Cube'])),
            'CustomSubmesh_1')

    def test_fills_a_gap_rather_than_appending(self):
        self.assertEqual(
            self.fn(self._context(['CustomSubmesh_0', 'CustomSubmesh_2'])),
            'CustomSubmesh_1')


class _FakeNamedObj:
    def __init__(self, name):
        self.name = name


class ValidateSubmeshNameTests(unittest.TestCase):
    def setUp(self):
        ns = _extract({'re', '_INVALID_SUBMESH_NAME_CHARS_RE', '_validate_submesh_name'})
        self.fn = ns['_validate_submesh_name']

    def _with_existing_objects(self, names):
        class _FakeBpyData:
            objects = set(names)

        class _FakeBpy:
            data = _FakeBpyData()

        self.fn.__globals__['bpy'] = _FakeBpy()

    def test_accepts_a_plain_name(self):
        self._with_existing_objects([])
        self.assertIsNone(self.fn('CustomSubmesh_3'))

    def test_rejects_empty_name(self):
        self._with_existing_objects([])
        self.assertIn('empty', self.fn('   '))

    def test_rejects_path_separators(self):
        self._with_existing_objects([])
        self.assertIsNotNone(self.fn('foo/bar'))

    def test_rejects_reserved_windows_characters(self):
        self._with_existing_objects([])
        for ch in '<>:"/\\|?*':
            self.assertIsNotNone(self.fn(f'name{ch}'), msg=ch)

    def test_rejects_name_already_used_by_an_object(self):
        self._with_existing_objects(['CustomSubmesh_0'])
        self.assertIn('already exists', self.fn('CustomSubmesh_0'))


class AddSubmeshOperatorStructureTests(unittest.TestCase):
    """AST checks that don't need bpy: registration, property names, and the
    dialog wiring described in PLAN_AddSubmesh.md Phase 5 step 3."""

    def setUp(self):
        self.source = PANEL_PATH.read_text(encoding='utf-8')
        self.tree = ast.parse(self.source)

    def _class(self, name):
        return next(
            node for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == name
        )

    def test_operator_has_expected_bl_idname(self):
        cls = self._class('SLUGGIES_OT_add_submesh')
        bl_idname = next(
            n.value.value for n in cls.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'bl_idname' for t in n.targets)
        )
        self.assertEqual(bl_idname, 'sluggies.add_submesh')

    def test_operator_declares_host_bone_and_template_source_properties(self):
        cls = self._class('SLUGGIES_OT_add_submesh')
        annotated_names = {
            n.target.id for n in cls.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
        }
        self.assertIn('host_bone', annotated_names)
        self.assertIn('template_source', annotated_names)
        self.assertIn('submesh_name', annotated_names)

    def test_invoke_defaults_submesh_name(self):
        cls = self._class('SLUGGIES_OT_add_submesh')
        invoke_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'invoke')
        invoke_src = ast.get_source_segment(self.source, invoke_fn)
        self.assertIn('_default_submesh_name', invoke_src)

    def test_execute_validates_and_uses_submesh_name(self):
        cls = self._class('SLUGGIES_OT_add_submesh')
        execute_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        execute_src = ast.get_source_segment(self.source, execute_fn)
        self.assertIn('_validate_submesh_name', execute_src)
        self.assertIn('_write_custom_submesh_texture(sluggie_path, submesh_name)', execute_src)
        self.assertIn('submesh_name', execute_src)

    def test_execute_selects_the_new_object(self):
        cls = self._class('SLUGGIES_OT_add_submesh')
        execute_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        execute_src = ast.get_source_segment(self.source, execute_fn)
        self.assertIn('_select_new_object', execute_src)

    def test_select_new_object_switches_to_object_mode_and_sets_active(self):
        fn = self._function('_select_new_object')
        fn_src = ast.get_source_segment(self.source, fn)
        self.assertIn("mode_set(mode='OBJECT')", fn_src)
        self.assertIn('select_set(True)', fn_src)
        self.assertIn('view_layer.objects.active', fn_src)

    def test_register_and_unregister_include_add_submesh_operator(self):
        register_src = ast.get_source_segment(self.source, self._function('register'))
        unregister_src = ast.get_source_segment(self.source, self._function('unregister'))
        self.assertIn('SLUGGIES_OT_add_submesh', register_src)
        self.assertIn('SLUGGIES_OT_add_submesh', unregister_src)

    def test_transfer_pose_and_animation_operators_are_removed(self):
        class_names = {n.name for n in self.tree.body if isinstance(n, ast.ClassDef)}
        self.assertNotIn('SLUGGIES_OT_transfer_pose', class_names)
        self.assertNotIn('SLUGGIES_OT_transfer_animation', class_names)
        self.assertNotIn('transfer_pose', self.source)
        self.assertNotIn('transfer_animation', self.source)

    def test_poll_requires_object_mode(self):
        cls = self._class('SLUGGIES_OT_add_submesh')
        poll_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'poll')
        poll_src = ast.get_source_segment(self.source, poll_fn)
        self.assertIn("context.mode != 'OBJECT'", poll_src)

    def test_invoke_reports_error_when_no_armature_is_active(self):
        cls = self._class('SLUGGIES_OT_add_submesh')
        invoke_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'invoke')
        invoke_src = ast.get_source_segment(self.source, invoke_fn)
        self.assertIn('_find_target_armature', invoke_src)
        self.assertIn('"ERROR"', invoke_src)
        self.assertIn('CANCELLED', invoke_src)

    def test_find_target_armature_never_scans_the_whole_scene(self):
        fn = self._function('_find_target_armature')
        fn_src = ast.get_source_segment(self.source, fn)
        self.assertNotIn('view_layer.objects', fn_src)

    def _function(self, name):
        return next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def test_panel_draw_adds_the_add_submesh_button(self):
        panel = self._class('SLUGGIES_PT_tools')
        draw_fn = next(n for n in panel.body if isinstance(n, ast.FunctionDef) and n.name == 'draw')
        draw_src = ast.get_source_segment(self.source, draw_fn)
        self.assertIn('SLUGGIES_OT_add_submesh.bl_idname', draw_src)

    def test_panel_draw_shows_the_free_host_bones_list(self):
        panel = self._class('SLUGGIES_PT_tools')
        draw_fn = next(n for n in panel.body if isinstance(n, ast.FunctionDef) and n.name == 'draw')
        draw_src = ast.get_source_segment(self.source, draw_fn)
        self.assertIn('_draw_free_host_bones', draw_src)

    def test_draw_free_host_bones_reuses_classify_host_bones_output(self):
        fn = self._function('_draw_free_host_bones')
        fn_src = ast.get_source_segment(self.source, fn)
        self.assertIn('_ordered_host_bone_choices', fn_src)

    def test_draw_free_host_bones_is_collapsible_and_collapsed_by_default(self):
        fn = self._function('_draw_free_host_bones')
        fn_src = ast.get_source_segment(self.source, fn)
        self.assertIn('sluggies_show_free_host_bones', fn_src)

        register_src = ast.get_source_segment(self.source, self._function('register'))
        self.assertIn('sluggies_show_free_host_bones', register_src)
        self.assertIn('default=False', register_src)

        unregister_src = ast.get_source_segment(self.source, self._function('unregister'))
        self.assertIn('sluggies_show_free_host_bones', unregister_src)


if __name__ == '__main__':
    unittest.main()
