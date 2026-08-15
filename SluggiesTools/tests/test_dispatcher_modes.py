import pathlib
import sys
import unittest


ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import start


class HammerspaceSectionArgsTests(unittest.TestCase):
    def test_surface_assignments_request_gpl_build(self):
        model = {'Submeshes': [{}, {'FaceSurfaceIdsEdited': 'AAE='}]}

        self.assertEqual(start.hammerspace_section_args(model), ['--gpl', 'build'])

    def test_unchanged_model_keeps_clone_defaults(self):
        self.assertEqual(start.hammerspace_section_args({'Submeshes': [{}]}), [])

    def test_other_edit_markers_do_not_activate_unfinished_rebuilders(self):
        model = {'Submeshes': [{'FacesDataEdited': 'AAAA'}]}

        self.assertEqual(start.hammerspace_section_args(model), [])


if __name__ == '__main__':
    unittest.main()