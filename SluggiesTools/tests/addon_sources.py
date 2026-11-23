"""Sources of the Blender add-on for tests that lift functions out of them without bpy.

The exporter, the importer and the tools panel are each a group of flat modules (``ExportSluggies.py`` and
the ``Export*`` modules it imports, and so on). A test that executes one function, or looks for a call in
"the exporter", should not have to know which module of the group holds it: ``EXPORT``, ``IMPORT`` and
``PANEL`` read as one source, in import order, and stand in for the path of the group's main module
(``read_text``, ``str``, ``parent``, ``is_file``).
"""

import ast
import pathlib

ADDON_DIR = pathlib.Path(__file__).resolve().parents[2] / 'BlenderAddonSrc'


class SourceGroup:
    """The modules of one group, the main module (the operator and ``register``) last."""

    def __init__(self, *names):
        self.names = names
        self.paths = [ADDON_DIR / f'{name}.py' for name in names]
        self.parent = ADDON_DIR

    def read_text(self, encoding='utf-8'):
        """The group as one source. The imports between its modules are left out: every name they bind is
        defined in this text, and a test that executes lifted statements has no package to import from."""
        parts = []
        for path in self.paths:
            text = path.read_text(encoding=encoding)
            lines = text.split('\n')
            for node in ast.parse(text).body:
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in self.names:
                    lines[node.lineno - 1:node.end_lineno] = [''] * (node.end_lineno - node.lineno + 1)
            parts.append('\n'.join(lines))
        return '\n\n'.join(parts)

    def is_file(self):
        return all(path.is_file() for path in self.paths)

    def __str__(self):
        return str(self.paths[-1])

    def __fspath__(self):
        return str(self.paths[-1])


EXPORT = SourceGroup(
    'ExportInplaceMesh', 'ExportBones', 'ExportFacial', 'ExportSkin', 'ExportTextures', 'ExportSurfaces',
    'ExportRebuildGlue', 'ExportSluggies')
IMPORT = SourceGroup('ImportDecode', 'ImportBones', 'SurfaceMaterials', 'ImportMesh', 'ImportAnm', 'ImportSluggies')
PANEL = SourceGroup(
    'SceneModels', 'HostBoneScene', 'SurfacePlans', 'EnumItems', 'AdaptSteps', 'SubmeshOps', 'BoneOps', 'BodyOps',
    'AnimScaleOp', 'AdaptAssignUI', 'AdaptMeshesUI', 'AdaptMaterialsUI', 'AdaptWeightsUI', 'AdaptLowpolyUI',
    'ExportHighLow', 'SluggiesToolsPanel')
