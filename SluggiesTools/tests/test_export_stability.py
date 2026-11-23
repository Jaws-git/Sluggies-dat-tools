"""Failure handling of the Blender exporter, on functions lifted from ``ExportSluggies.py`` (no bpy needed):
facial pose values that do not fit raise instead of being clamped, a degenerate loop normal finds its face, the
rest-pose context always restores the pose setting, the length check no longer compares the donor with itself,
and both ``.sluggie`` writers leave the old file whole when the write fails."""

import ast
import contextlib
import math
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import addon_sources  # noqa: E402
EXPORT_PATH = addon_sources.EXPORT
for path in (EXPORT_PATH.parent, ROOT_DIR / 'SluggiesTools'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
import FieldCodec  # noqa: E402
import binfmt  # noqa: E402

NAMES = {
    'math', 'struct', 'contextlib',
    '_to_bytes', '_from_bytes', '_pack_quantized_component', '_encode_facial_key', '_per_loop_normals',
    '_armature_rest_pose', 'detect_length_mismatches',
}


def _binds_name(node):
    if isinstance(node, ast.FunctionDef):
        return node.name in NAMES
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((a.asname or a.name) in NAMES for a in node.names)
    return False


def _export_functions():
    tree = ast.parse(EXPORT_PATH.read_text(encoding='utf-8'))
    namespace = {'FieldCodec': FieldCodec}
    body = [node for node in tree.body if _binds_name(node)]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(EXPORT_PATH), 'exec'), namespace)
    return SimpleNamespace(**namespace)


EXPORT = _export_functions()


class FacialPoseRangeTests(unittest.TestCase):
    QUANT = 0x08                                     # s16, 8 fraction bits: representable -128 .. 127.996

    def _encode(self, coordinate, basis=None):
        """Pose zero from *coordinate*, or (with *basis*) a later pose's offset from that Basis position."""
        key = SimpleNamespace(name='smile', data=[SimpleNamespace(co=coordinate)])
        basis_key = SimpleNamespace(name='Basis', data=[SimpleNamespace(co=basis or coordinate)])
        zero = FieldCodec.encode_field(struct.pack('>3h', 0, 0, 0), False)
        return EXPORT._encode_facial_key(key, basis_key, zero, [0], 1 if basis else 0, self.QUANT, 3, 2)

    def test_a_pose_is_its_offset_from_the_basis_key_of_this_export(self):
        """Not from the file's earlier pose zero: Blender moves the keys along with a Basis edit, and that
        edit must not land in every pose a second time."""
        moved_basis = (1.0, 2.0, 3.5)                    # the user moved the Basis by +0.5 z; the key followed
        self.assertEqual(self._encode((1.25, 2.0, 3.5), basis=moved_basis), struct.pack('>3h', 64, 0, 0))

    def test_values_in_range_are_quantized(self):
        self.assertEqual(self._encode((1.0, -2.0, 0.5)), struct.pack('>3h', 256, -512, 128))

    def test_a_value_out_of_range_is_refused_not_clamped(self):
        with self.assertRaisesRegex(ValueError, 'shape key smile vertex 0 y.*outside int16'):
            self._encode((1.0, 500.0, 0.0))

    def test_a_value_that_is_not_finite_names_its_vertex(self):
        with self.assertRaisesRegex(ValueError, 'shape key smile vertex 0 z.*not finite'):
            self._encode((0.0, 0.0, math.nan))


class _Normal(tuple):
    @property
    def length_squared(self):
        return sum(v * v for v in self)


class LoopNormalFallbackTests(unittest.TestCase):
    def test_a_degenerate_loop_normal_takes_its_face_normal(self):
        """A mesh loop does not name its polygon; the map comes from the polygons' loop lists."""
        loops = [SimpleNamespace(normal=_Normal(n)) for n in ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 0, 0))]
        polygons = [SimpleNamespace(index=0, loop_indices=(0, 1), normal=_Normal((0, 1, 0))),
                    SimpleNamespace(index=1, loop_indices=(2, 3), normal=_Normal((0, -1, 0)))]
        mesh = SimpleNamespace(loops=loops, polygons=polygons)
        self.assertEqual(EXPORT._per_loop_normals(mesh, [0, 1, 2, 3]),
                         [(0, 0, 1), (0, 1, 0), (1, 0, 0), (0, -1, 0)])


class RestPoseContextTests(unittest.TestCase):
    def setUp(self):
        self.updates = 0
        self.context = SimpleNamespace(view_layer=SimpleNamespace(update=self._update))
        self.arm = SimpleNamespace(data=SimpleNamespace(pose_position='POSE'))

    def _update(self):
        self.updates += 1
        if self.failing:
            raise RuntimeError('depsgraph')

    failing = False

    def test_the_pose_setting_comes_back(self):
        with EXPORT._armature_rest_pose(self.context, self.arm):
            self.assertEqual(self.arm.data.pose_position, 'REST')
            with EXPORT._armature_rest_pose(self.context, self.arm):       # nested: nothing more to do
                self.assertEqual(self.updates, 1)
            self.assertEqual(self.arm.data.pose_position, 'REST')
        self.assertEqual((self.arm.data.pose_position, self.updates), ('POSE', 2))

    def test_it_comes_back_when_the_first_update_fails(self):
        self.failing = True
        with contextlib.suppress(RuntimeError):
            with EXPORT._armature_rest_pose(self.context, self.arm):
                raise AssertionError("not reached")
        self.assertEqual(self.arm.data.pose_position, 'POSE')

    def test_an_armature_the_user_keeps_in_rest_is_left_alone(self):
        self.arm.data.pose_position = 'REST'
        with EXPORT._armature_rest_pose(self.context, self.arm):
            pass
        self.assertEqual((self.arm.data.pose_position, self.updates), ('REST', 0))


class LengthCheckTests(unittest.TestCase):
    def _submesh(self):
        faces = FieldCodec.encode_field(struct.pack('>3H', 0, 1, 2), False)      # uses UV slots 0-2 of 4
        return {'VertexBuffer': {'VertexBufferCompCount': 3, 'VertexBufferQuantizeInfo': 0x08,
                                 'VertexBufferLength': 3 * 6},
                'UVChannels': [{'UVChannelIndex': 0, 'UVChannelCompCount': 2, 'UVChannelQuantizeInfo': 0x08,
                                'UVChannelLength': 4 * 4, 'UVFacesData': faces}]}

    def test_a_donor_with_an_unused_uv_slot_is_not_a_changed_mesh(self):
        obj = SimpleNamespace(data=SimpleNamespace(vertices=[0, 1, 2]))
        self.assertEqual(EXPORT.detect_length_mismatches(obj, self._submesh()), [])

    def test_a_changed_vertex_count_is_still_found(self):
        obj = SimpleNamespace(data=SimpleNamespace(vertices=[0, 1, 2, 3]))
        issues = EXPORT.detect_length_mismatches(obj, self._submesh())
        self.assertEqual(len(issues), 1)
        self.assertIn('vertex count changed', issues[0])


class ExportPhaseTests(unittest.TestCase):
    """``execute`` runs phase methods; a phase stops the export only by raising ``ExportCancelled``."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(EXPORT_PATH.read_text(encoding='utf-8'))
        operator = next(node for node in cls.tree.body
                        if isinstance(node, ast.ClassDef) and node.name == 'SLUGGIES_OT_export')
        cls.methods = {node.name: node for node in operator.body if isinstance(node, ast.FunctionDef)}

    def _cancelled_class(self):
        node = next(node for node in self.tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'ExportCancelled')
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(EXPORT_PATH), 'exec'), namespace)
        return namespace['ExportCancelled']

    def test_cancel_carries_one_or_several_messages(self):
        cancelled = self._cancelled_class()
        self.assertEqual(cancelled('one').messages, ['one'])
        self.assertEqual(cancelled(['one', 'two']).messages, ['one', 'two'])
        # The phases turn an encoder's ValueError into a cancel inside `except ValueError` blocks.
        self.assertFalse(issubclass(cancelled, ValueError))

    def test_execute_runs_every_phase_and_catches_the_cancel_once(self):
        execute = self.methods['execute']
        called = [node.func.attr for node in ast.walk(execute)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self'
                  and node.func.attr.startswith('_')]
        self.assertGreater(len(called), 10)
        self.assertEqual(len(called), len(set(called)))
        # The phases are defined after `execute`, in the order they run (the wiring tests rely on it).
        phases_in_file = [name for name in self.methods if name in called]
        self.assertEqual(phases_in_file, called)
        handlers = [node for node in ast.walk(execute) if isinstance(node, ast.ExceptHandler)]
        self.assertEqual([ast.unparse(handler.type) for handler in handlers], ['ExportCancelled'])

    def test_no_phase_returns_cancelled_or_reports_an_error_itself(self):
        names = list(self.methods)
        for name in names[names.index('execute') + 1:]:
            source = ast.unparse(self.methods[name])
            self.assertNotIn('CANCELLED', source, name)
            if name != '_report_notes':                 # it reports the collected non-fatal errors
                self.assertNotIn("self.report({'ERROR'}", source, name)

    def test_execute_and_its_phases_stay_short(self):
        names = list(self.methods)
        for name in names[names.index('execute'):]:
            node = self.methods[name]
            self.assertLessEqual(node.end_lineno - node.lineno + 1, 100, name)


class AtomicSluggieWriteTests(unittest.TestCase):
    WRITERS = (('binfmt', binfmt), ('add-on', FieldCodec))

    def test_a_failed_write_leaves_the_old_file_whole(self):
        old = {'SluggiesModel': {'UseCompression': False, 'Name': 'old'}}
        new = {'SluggiesModel': {'UseCompression': False, 'Name': 'new'}}
        for name, codec in self.WRITERS:
            with self.subTest(writer=name), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'model.sluggie')
                codec.dump_sluggie(old, path)
                with mock.patch.object(codec.os, 'replace', side_effect=PermissionError('locked')):
                    with self.assertRaises(PermissionError):
                        codec.dump_sluggie(new, path)
                self.assertEqual(codec.load_sluggie(path), old)
                self.assertEqual(os.listdir(tmp), ['model.sluggie'])           # no temp file left
                codec.dump_sluggie(new, path)
                self.assertEqual(codec.load_sluggie(path), new)
                self.assertEqual(os.listdir(tmp), ['model.sluggie'])


if __name__ == '__main__':
    unittest.main()
