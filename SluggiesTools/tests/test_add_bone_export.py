"""PLAN_AddBones.md Phase 4 step 3: BoneHierarchyEdited export.

AST-lift + exec tests for ExportSluggies.py, mirroring
test_reassign_bone_operator.py's conventions -- no bpy/mathutils import
required. A minimal fake Matrix (pure-translation composition only) stands in
for mathutils.Matrix, which is not installed in this test environment.
"""

import ast
import pathlib
import unittest

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
EXPORT_PATH = ROOT_DIR / 'BlenderAddonSrc' / 'ExportSluggies.py'

GEO_ID_FREE = 0xFFFF


def _binds_name(node, names):
    if isinstance(node, ast.FunctionDef):
        return node.name in names
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((a.asname or a.name) in names for a in node.names)
    return False


def _extract(names, path=EXPORT_PATH, extra_globals=None):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if _binds_name(node, names)]
    module = ast.Module(body=nodes, type_ignores=[])
    namespace = dict(extra_globals or {})
    exec(compile(module, str(EXPORT_PATH), 'exec'), namespace)
    return namespace


class _FakeVec3:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class _FakeQuat:
    def __init__(self, w=1.0, x=0.0, y=0.0, z=0.0):
        self.w, self.x, self.y, self.z = w, x, y, z


class _FakeMatrix:
    """Only supports pure-translation composition -- enough to exercise
    encode_bone_hierarchy_edited's id-assignment and field-mapping logic
    without a real mathutils dependency."""

    def __init__(self, translation=(0.0, 0.0, 0.0)):
        self.translation = translation

    def inverted(self):
        x, y, z = self.translation
        return _FakeMatrix((-x, -y, -z))

    def __matmul__(self, other):
        ax, ay, az = self.translation
        bx, by, bz = other.translation
        return _FakeMatrix((ax + bx, ay + by, az + bz))

    def decompose(self):
        x, y, z = self.translation
        return _FakeVec3(x, y, z), _FakeQuat(1.0, 0.0, 0.0, 0.0), _FakeVec3(1.0, 1.0, 1.0)


class _FakeBone:
    def __init__(self, name, parent=None, matrix_local=None, **props):
        self.name = name
        self.parent = parent
        self.matrix_local = matrix_local if matrix_local is not None else _FakeMatrix()
        self._props = props

    def get(self, key, default=None):
        return self._props.get(key, default)


class _FakeBones(list):
    def get(self, name, default=None):
        for b in self:
            if b.name == name:
                return b
        return default

    def __contains__(self, name):
        return any(b.name == name for b in self)


class _FakeArmatureData:
    def __init__(self, bones):
        self.bones = _FakeBones(bones)


class _FakeArmObj:
    def __init__(self, bones, name='Armature'):
        self.type = 'ARMATURE'
        self.name = name
        self.data = _FakeArmatureData(bones)
        self.parent = None
        self._props = {'RootBoneScale': (1.0, 1.0, 1.0)}

    def get(self, key, default=None):
        return self._props.get(key, default)

    def keys(self):
        return self._props.keys()

    def __contains__(self, key):
        return key in self._props


class _FakeScene:
    objects = []


class _FakeContext:
    def __init__(self):
        self.scene = _FakeScene()


class BoneIdFromNameTests(unittest.TestCase):
    def setUp(self):
        self.fn = _extract({'re', '_BONE_NAME_RE', '_bone_id_from_bone_name'})['_bone_id_from_bone_name']

    def test_matches(self):
        self.assertEqual(self.fn('bone_12'), 12)

    def test_no_match(self):
        self.assertIsNone(self.fn('spine'))
        self.assertIsNone(self.fn(None))


class ArmatureHasNewBonesTests(unittest.TestCase):
    def setUp(self):
        ns = _extract(
            {'_find_root_scale_armature', 'armature_has_new_bones'},
        )
        self.fn = ns['armature_has_new_bones']

    def test_false_without_new_bones(self):
        arm = _FakeArmObj([_FakeBone('bone_0')])
        self.assertFalse(self.fn([arm], _FakeContext()))

    def test_true_with_new_bone(self):
        arm = _FakeArmObj([_FakeBone('bone_0'), _FakeBone('bone_1', SluggiesUserAdded=True)])
        self.assertTrue(self.fn([arm], _FakeContext()))

    def test_false_without_armature(self):
        self.assertFalse(self.fn([], _FakeContext()))


class EncodeBoneHierarchyEditedTests(unittest.TestCase):
    def setUp(self):
        ns = _extract(
            {'re', '_BONE_NAME_RE', '_bone_id_from_bone_name',
             '_find_root_scale_armature', 'encode_bone_hierarchy_edited',
             'SRT_TYPE_ROTATION', 'SRT_TYPE_TRANSLATION', '_srt_type_for'},
            extra_globals={'GEO_ID_FREE': GEO_ID_FREE},
        )
        self.fn = ns['encode_bone_hierarchy_edited']

    def _donor_bone(self, bone_id, parent_id=None):
        return {
            "BoneId": bone_id, "GeoId": 0, "GeoIdRaw": GEO_ID_FREE,
            "ParentBoneId": parent_id, "Skinned": False, "TrackId": GEO_ID_FREE,
            "MirrorBoneId": bone_id, "MirrorRole": 3, "SRTType": 0xC,
            "SRTOffset": None, "DrawPriority": 0, "InheritTransform": True,
            "Translation": [0.0, 0.0, 0.0], "Scale": [1.0, 1.0, 1.0],
            "Quaternion": [-1.0, 0.0, 0.0, 0.0], "HeadPosition": [0.0, 0.0, 0.0],
            "VertexInfluences": [],
        }

    def test_no_new_bones_clears_field(self):
        data = {"SluggiesModel": {"BoneHierarchy": [self._donor_bone(0)],
                                   "BoneHierarchyEdited": [{"stale": True}]}}
        arm = _FakeArmObj([_FakeBone('bone_0')])
        warnings = []
        self.fn([arm], data, warnings, _FakeContext())
        self.assertNotIn("BoneHierarchyEdited", data["SluggiesModel"])

    def test_new_leaf_bone_appended_with_contiguous_id(self):
        donor0 = _FakeBone('bone_0')
        new_bone = _FakeBone(
            'bone_5', parent=donor0, matrix_local=_FakeMatrix((0.0, 0.0, 0.3)),
            SluggiesUserAdded=True, SluggiesCreationOrder=0,
            SluggiesGeoIdRaw=GEO_ID_FREE, SluggiesSkinned=False,
            SluggiesSRTType=0xC, SluggiesDrawPriority=0,
            SluggiesInheritTransform=True, track_id=GEO_ID_FREE,
        )
        arm = _FakeArmObj([donor0, new_bone])
        data = {"SluggiesModel": {"BoneHierarchy": [self._donor_bone(0)]}}
        warnings = []
        self.fn([arm], data, warnings, _FakeContext())

        edited = data["SluggiesModel"]["BoneHierarchyEdited"]
        self.assertEqual(len(edited), 2)
        self.assertFalse(edited[0]["UserAdded"])
        self.assertEqual(edited[0]["BoneId"], 0)

        new_entry = edited[1]
        self.assertTrue(new_entry["UserAdded"])
        self.assertEqual(new_entry["BoneId"], 1)  # donor_count(1) + creation order 0
        self.assertEqual(new_entry["ParentBoneId"], 0)
        self.assertEqual(new_entry["MirrorBoneId"], new_entry["BoneId"])
        self.assertEqual(new_entry["MirrorRole"], 3)
        self.assertEqual(new_entry["TrackId"], GEO_ID_FREE)
        self.assertEqual(new_entry["Translation"], [0.0, 0.0, 0.3])
        self.assertEqual(new_entry["Scale"], [1.0, 1.0, 1.0])

    def test_creation_order_picks_id_not_blender_name(self):
        donor0 = _FakeBone('bone_0')
        # Named as if it were id 9, but creation order 0 must still win id 1.
        new_bone = _FakeBone(
            'bone_9', parent=donor0, SluggiesUserAdded=True, SluggiesCreationOrder=0,
        )
        arm = _FakeArmObj([donor0, new_bone])
        data = {"SluggiesModel": {"BoneHierarchy": [self._donor_bone(0)]}}
        self.fn([arm], data, [], _FakeContext())
        new_entry = data["SluggiesModel"]["BoneHierarchyEdited"][1]
        self.assertEqual(new_entry["BoneId"], 1)

    def test_chained_new_bones_sorted_by_creation_order(self):
        donor0 = _FakeBone('bone_0')
        second = _FakeBone('bone_2', parent=donor0, SluggiesUserAdded=True, SluggiesCreationOrder=1)
        first = _FakeBone('bone_1', parent=donor0, SluggiesUserAdded=True, SluggiesCreationOrder=0)
        second.parent = first  # chained: second's real parent is the first new bone
        arm = _FakeArmObj([donor0, second, first])
        data = {"SluggiesModel": {"BoneHierarchy": [self._donor_bone(0)]}}
        self.fn([arm], data, [], _FakeContext())
        edited_by_name = {b["BoneId"]: b for b in data["SluggiesModel"]["BoneHierarchyEdited"]}
        first_entry = next(b for b in data["SluggiesModel"]["BoneHierarchyEdited"] if b["UserAdded"] and b["ParentBoneId"] == 0)
        second_entry = next(b for b in data["SluggiesModel"]["BoneHierarchyEdited"] if b["UserAdded"] and b["ParentBoneId"] == first_entry["BoneId"])
        self.assertEqual(first_entry["BoneId"], 1)
        self.assertEqual(second_entry["BoneId"], 2)


class SrtTypeForTests(unittest.TestCase):
    """PLAN_AddBones.md F11: the SRT type byte is a component-presence mask, so
    it has to be derived from the rotation/translation actually written. A bone
    that inherited a translation-only 8 from its parent while carrying a real
    rotation had that rotation dropped in game, moving any mesh riding it."""

    def setUp(self):
        ns = _extract({'SRT_TYPE_ROTATION', 'SRT_TYPE_TRANSLATION', '_srt_type_for'})
        self.fn = ns['_srt_type_for']

    def _call(self, translation, rotation, scale=(1.0, 1.0, 1.0), warnings=None):
        return self.fn(
            'bone_91', _FakeVec3(*translation), _FakeQuat(*rotation),
            _FakeVec3(*scale), [] if warnings is None else warnings,
        )

    def test_identity_bone_claims_nothing(self):
        self.assertEqual(self._call((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)), 0)

    def test_translation_only_claims_bit_8(self):
        self.assertEqual(self._call((0.0, 0.11, 0.0), (1.0, 0.0, 0.0, 0.0)), 0x8)

    def test_rotation_only_claims_bit_4(self):
        self.assertEqual(self._call((0.0, 0.0, 0.0), (0.0, 0.0, -0.7071, -0.7071)), 0x4)

    def test_rotation_and_translation_claim_both(self):
        self.assertEqual(self._call((0.0, 0.11, 0.0), (0.0, 0.0, -0.7071, -0.7071)), 0xC)

    def test_negated_w_identity_is_still_identity(self):
        # Quaternions are stored with w negated, so -1 is as much an identity
        # rotation as +1 and must not set the rotation bit.
        self.assertEqual(self._call((0.0, 0.11, 0.0), (-1.0, 0.0, 0.0, 0.0)), 0x8)

    def test_non_unit_scale_warns_without_changing_the_mask(self):
        warnings = []
        self.assertEqual(
            self._call((0.0, 0.11, 0.0), (1.0, 0.0, 0.0, 0.0), (2.0, 1.0, 1.0), warnings),
            0x8,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn('scale', warnings[0])


class ValidateBoneHierarchyEditedExportTests(unittest.TestCase):
    def setUp(self):
        self.fn = _extract({'validate_bone_hierarchy_edited_export'})['validate_bone_hierarchy_edited_export']

    def _donor(self, bone_id, parent_id=None):
        return {"BoneId": bone_id, "ParentBoneId": parent_id}

    def test_no_edited_field_is_clean(self):
        self.assertEqual(self.fn({"BoneHierarchy": [self._donor(0)]}, []), [])

    def test_deleted_donor_bone_is_rejected(self):
        model = {"BoneHierarchy": [self._donor(0), self._donor(1, 0)],
                  "BoneHierarchyEdited": [dict(self._donor(0), UserAdded=False)]}
        errors = self.fn(model, [])
        self.assertTrue(any('missing' in e for e in errors))

    def test_reparented_donor_bone_is_rejected(self):
        model = {
            "BoneHierarchy": [self._donor(0), self._donor(1, 0), self._donor(2, 1)],
            "BoneHierarchyEdited": [
                dict(self._donor(0), UserAdded=False),
                dict(self._donor(1, parent_id=2), UserAdded=False),
                dict(self._donor(2, 1), UserAdded=False),
            ],
        }
        errors = self.fn(model, [])
        self.assertTrue(any('reparented' in e for e in errors))

    def test_new_bone_spliced_mid_chain_is_rejected(self):
        model = {
            "BoneHierarchy": [self._donor(0), self._donor(1, 0)],
            "BoneHierarchyEdited": [
                dict(self._donor(0), UserAdded=False),
                dict(self._donor(1, parent_id=2), UserAdded=False),
                dict(BoneId=2, ParentBoneId=0, UserAdded=True),
            ],
        }
        errors = self.fn(model, [])
        self.assertTrue(any('inserted between' in e for e in errors))

    def test_new_root_bone_is_rejected(self):
        model = {
            "BoneHierarchy": [self._donor(0)],
            "BoneHierarchyEdited": [
                dict(self._donor(0), UserAdded=False),
                dict(BoneId=1, ParentBoneId=None, UserAdded=True),
            ],
        }
        errors = self.fn(model, [])
        self.assertTrue(any('may not be roots' in e for e in errors))

    def test_valid_leaf_append_has_no_errors(self):
        model = {
            "BoneHierarchy": [self._donor(0)],
            "BoneHierarchyEdited": [
                dict(self._donor(0), UserAdded=False),
                dict(BoneId=1, ParentBoneId=0, UserAdded=True),
            ],
        }
        self.assertEqual(self.fn(model, []), [])


if __name__ == '__main__':
    unittest.main()
