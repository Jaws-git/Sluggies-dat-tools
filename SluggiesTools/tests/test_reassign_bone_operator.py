"""PLAN_EditRigidMeshes.md Phase 6: the Reassign to new bone button.

AST checks and bpy-free pure-helper tests for SluggiesToolsPanel.py, mirroring
test_add_submesh_operator.py's conventions -- no bpy import required.
"""

import ast
import pathlib
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


def _extract(names, path=PANEL_PATH, extra_globals=None):
    """Load the given top-level names from SluggiesToolsPanel.py into an
    executable module, in source order, without importing bpy/bmesh."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if _binds_name(node, names)]
    module = ast.Module(body=nodes, type_ignores=[])
    namespace = dict(extra_globals or {})
    exec(compile(module, str(PANEL_PATH), 'exec'), namespace)
    return namespace


class _FakeObj:
    def __init__(self, name='obj', **props):
        self.name = name
        self._props = props

    def get(self, key, default=None):
        return self._props.get(key, default)

    def keys(self):
        return self._props.keys()


class RigidMeshKindTests(unittest.TestCase):
    def setUp(self):
        ns = _extract({'re', '_rigid_mesh_kind', '_SURFACE_ID_RE', '_submesh_index_of'})
        self.fn = ns['_rigid_mesh_kind']

    def test_none_for_non_mesh(self):
        obj = _FakeObj()
        obj.type = 'ARMATURE'
        self.assertEqual(self.fn(obj), (None, None))

    def test_custom_submesh(self):
        obj = _FakeObj(SluggiesCustomSubmesh=True)
        obj.type = 'MESH'
        self.assertEqual(self.fn(obj), ('custom', None))

    def test_rigid_donor_submesh_reads_submesh_index_from_material(self):
        obj = _FakeObj(VertexBufferCompCount=3)
        obj.type = 'MESH'

        class _Mat:
            def get(self, key, default=None):
                return {'SurfaceId': 'sm2_ds5'}.get(key, default)
        obj.material_slots = [type('Slot', (), {'material': _Mat()})()]
        self.assertEqual(self.fn(obj), ('donor', 2))

    def test_skinned_submesh_is_neither(self):
        obj = _FakeObj(VertexBufferCompCount=6)
        obj.type = 'MESH'
        self.assertEqual(self.fn(obj), (None, None))

    def test_none_object(self):
        self.assertEqual(self.fn(None), (None, None))


class PlacementEnumItemsTests(unittest.TestCase):
    def setUp(self):
        ns = _extract({
            '_placement_enum_items', '_PLACEMENT_KEEP_WORLD', '_PLACEMENT_KEEP_OFFSET',
        })
        self.fn = ns['_placement_enum_items']

    def _context(self, obj):
        class _Context:
            pass
        ctx = _Context()
        ctx.active_object = obj
        return ctx

    def test_both_placements_offered_by_default(self):
        obj = _FakeObj()
        items = self.fn(None, self._context(obj))
        self.assertEqual([i[0] for i in items], ['KEEP_WORLD', 'KEEP_OFFSET'])

    def test_facial_shapekeys_restrict_to_keep_offset(self):
        obj = _FakeObj(FacialShapeKeyCount=2)
        items = self.fn(None, self._context(obj))
        self.assertEqual([i[0] for i in items], ['KEEP_OFFSET'])

    def test_no_active_object_offers_both(self):
        items = self.fn(None, self._context(None))
        self.assertEqual([i[0] for i in items], ['KEEP_WORLD', 'KEEP_OFFSET'])


class ReadBoneHierarchyTests(unittest.TestCase):
    def setUp(self):
        ns = _extract({'json', '_read_bone_hierarchy'})
        self.fn = ns['_read_bone_hierarchy']

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.fn('/does/not/exist.sluggie'))

    def test_reads_bone_hierarchy(self):
        import json
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'model.sluggie')
            with open(path, 'w') as f:
                json.dump({'SluggiesModel': {'BoneHierarchy': [{'BoneId': 1}]}}, f)
            self.assertEqual(self.fn(path), [{'BoneId': 1}])


class ReassignBoneOperatorStructureTests(unittest.TestCase):
    """AST checks that don't need bpy: registration, property names, poll
    conditions and dialog/execute wiring described in
    PLAN_EditRigidMeshes.md's 'Reassign to new bone (concept)' section."""

    def setUp(self):
        self.source = PANEL_PATH.read_text(encoding='utf-8')
        self.tree = ast.parse(self.source)

    def _class(self, name):
        return next(
            node for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == name
        )

    def _function(self, name):
        return next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def test_operator_has_expected_bl_idname(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        bl_idname = next(
            n.value.value for n in cls.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'bl_idname' for t in n.targets)
        )
        self.assertEqual(bl_idname, 'sluggies.reassign_bone')

    def test_operator_has_undo_option(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        src = ast.get_source_segment(self.source, cls)
        self.assertIn('bl_options = {"UNDO"}', src)

    def test_operator_declares_target_bone_and_placement_properties(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        annotated_names = {
            n.target.id for n in cls.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
        }
        self.assertIn('target_bone', annotated_names)
        self.assertIn('placement', annotated_names)

    def test_poll_requires_object_mode(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        poll_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'poll')
        poll_src = ast.get_source_segment(self.source, poll_fn)
        self.assertIn("context.mode != 'OBJECT'", poll_src)

    def test_poll_disables_skinned_submeshes_with_a_reason(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        poll_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'poll')
        poll_src = ast.get_source_segment(self.source, poll_fn)
        self.assertIn("VertexBufferCompCount", poll_src)
        self.assertIn('== 6', poll_src)
        self.assertIn('vertex groups', poll_src)

    def test_poll_requires_current_bone_metadata(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        poll_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'poll')
        poll_src = ast.get_source_segment(self.source, poll_fn)
        self.assertIn('_bone_metadata_is_current(arm_obj)', poll_src)
        self.assertIn('HostBones.RE_IMPORT_MESSAGE', poll_src)

    def test_invoke_defaults_target_bone_and_opens_dialog(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        invoke_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'invoke')
        invoke_src = ast.get_source_segment(self.source, invoke_fn)
        self.assertIn('_default_reassign_target', invoke_src)
        self.assertIn('invoke_props_dialog', invoke_src)

    def test_execute_rechecks_free_bones_before_applying(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        execute_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        execute_src = ast.get_source_segment(self.source, execute_fn)
        self.assertIn('_reassign_choices(context, arm_obj, obj)', execute_src)
        self.assertIn('no longer free', execute_src)

    def test_execute_replaces_bone_vertex_groups_for_all_vertices(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        execute_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        execute_src = ast.get_source_segment(self.source, execute_fn)
        self.assertIn('vertex_groups.remove', execute_src)
        self.assertIn("vertex_groups.new(name=f'bone_{target_bone_id}')", execute_src)
        self.assertIn("new_vg.add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')", execute_src)

    def test_execute_applies_keep_offset_matrix_only_for_that_placement(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        execute_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        execute_src = ast.get_source_segment(self.source, execute_fn)
        self.assertIn("self.placement == 'KEEP_OFFSET'", execute_src)
        self.assertIn('keep_offset_world_matrix', execute_src)
        self.assertIn('bone_absolute_matrices', execute_src)

    def test_execute_ensures_armature_modifier(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        execute_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        execute_src = ast.get_source_segment(self.source, execute_fn)
        self.assertIn("m.type == 'ARMATURE'", execute_src)
        self.assertIn('mod.object = arm_obj', execute_src)

    def test_execute_stores_placement_for_reporting_only(self):
        cls = self._class('SLUGGIES_OT_reassign_bone')
        execute_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        execute_src = ast.get_source_segment(self.source, execute_fn)
        self.assertIn("obj['SluggiesRigidPlacement'] = self.placement", execute_src)

    def test_register_and_unregister_include_reassign_bone_operator(self):
        register_src = ast.get_source_segment(self.source, self._function('register'))
        unregister_src = ast.get_source_segment(self.source, self._function('unregister'))
        self.assertIn('SLUGGIES_OT_reassign_bone', register_src)
        self.assertIn('SLUGGIES_OT_reassign_bone', unregister_src)

    def test_panel_draws_the_rigid_mesh_box(self):
        panel = self._class('SLUGGIES_PT_tools')
        draw_fn = next(n for n in panel.body if isinstance(n, ast.FunctionDef) and n.name == 'draw')
        draw_src = ast.get_source_segment(self.source, draw_fn)
        self.assertIn('_draw_rigid_mesh_box', draw_src)

    def test_draw_rigid_mesh_box_includes_the_operator(self):
        fn = self._function('_draw_rigid_mesh_box')
        fn_src = ast.get_source_segment(self.source, fn)
        self.assertIn('SLUGGIES_OT_reassign_bone.bl_idname', fn_src)


if __name__ == '__main__':
    unittest.main()
