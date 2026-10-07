"""Multi-surface custom submeshes (PLAN_EditRigidMeshes.md Phase 9):
``CustomSubmeshes[].AdditionalSurfaces`` + ``FaceSurfaceIndices`` through the
validator, the parser, the builder (on the synthetic donor), the dispatcher,
and the bpy-free exporter helpers that produce them."""
import base64
import pathlib
import struct
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT_DIR / 'SluggiesTools'
ADDON_DIR = ROOT_DIR / 'BlenderAddonSrc'
for path in (ROOT_DIR, TOOLS_DIR, TOOLS_DIR / 'Hammerspace', ADDON_DIR, pathlib.Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import CustomSubmeshExport as cse  # noqa: E402
import HammerspaceMain as main  # noqa: E402
import RigidRebuildExport as rre  # noqa: E402
import start  # noqa: E402
import synthetic_donor  # noqa: E402
import texture_helper  # noqa: E402
import test_custom_submesh_export as tcs  # noqa: E402
import test_rigid_rebuild as trr  # noqa: E402
import test_rigid_rebuild_export as tre  # noqa: E402

CS_ID = 'custom0'
PRIMARY = f'{CS_ID}_ds0'
NEW0 = f'{CS_ID}_new0'
NEW1 = f'{CS_ID}_new1'
DONOR_SURFACE = f'sm{synthetic_donor.RIGID_OWNER_SUBMESH}_ds{synthetic_donor.SURFACE_STATE_INDEX}'


def _u16s(values) -> str:
    return base64.b64encode(struct.pack(f'>{len(values)}H', *values)).decode('ascii')


def _surface(key=NEW0, template='builtin:rigid_spec_v1', texture=None, **extra) -> dict:
    entry = {
        'SurfaceKey': key,
        'MaterialName': f'mat_{key}',
        'TemplateSource': template,
        'TextureAssignment': texture if texture is not None else {'DonorTextureIndex': 1},
    }
    entry.update(extra)
    return entry


class _DonorCase(unittest.TestCase):
    """A synthetic-donor model with one exported cube custom submesh."""

    def setUp(self):
        self.env = self.enterContext(synthetic_donor.donor_environment())
        self.data = self.env.reload()
        self.model = self.data['SluggiesModel']
        self.model['UseHammerspace'] = True
        self.main = main
        self.host_bone_id = tcs.ExportedEntryBuildsTests._free_host_bone(self, self.model)
        host_bind = cse.bone_absolute_matrices(self.model['BoneHierarchy'])[self.host_bone_id]
        geometry = tcs._cube_geometry(obj_world=tcs._mul(tcs.ARMATURE_WORLD, host_bind), host_bind=host_bind)
        normals, uvs, colors = tcs._cube_loop_attributes()
        plan = cse.attribute_plan(self.model, 'builtin:rigid_spec_v1')
        self.entry = cse.build_custom_submesh_entry(
            'CustomSubmesh_1', CS_ID, self.host_bone_id, 'builtin:rigid_spec_v1', plan,
            geometry, normals, uvs, colors, {'DonorTextureIndex': 0},
        )
        self.model['CustomSubmeshes'] = [self.entry]
        self.faces = self.entry['FacesCount']

    def _build(self, **modes):
        modes = dict(dict(gpl='build', act='clone', tex='clone', skn='clone', trailing='clone'), **modes)
        result = main.BuildModelBlock(self.data, main.SectionModes(**modes), sluggie_path=self.env.sluggie_path)
        self.assertTrue(result.validation_report['valid'], result.validation_report.get('errors'))
        return result, trr._gpl_section(result.block)


class ValidatorTests(_DonorCase):
    def _errors(self):
        try:
            main._validate_custom_submeshes(self.model)
        except ValueError as exc:
            return str(exc)
        return ''

    def test_valid_two_surface_entry_passes(self):
        self.entry['AdditionalSurfaces'] = [_surface()]
        self.entry['FaceSurfaceIndices'] = _u16s([0] * (self.faces - 1) + [1])
        self.assertEqual(self._errors(), '')

    def test_no_additional_surfaces_needs_no_indices(self):
        self.assertEqual(self._errors(), '')
        self.entry['AdditionalSurfaces'] = []
        self.assertEqual(self._errors(), '')

    def test_key_must_belong_to_this_custom_submesh(self):
        self.entry['AdditionalSurfaces'] = [_surface(key='sm1_new0')]
        self.entry['FaceSurfaceIndices'] = _u16s([1] * self.faces)
        self.assertIn(f'key must match {CS_ID}_new<K>', self._errors())

    def test_duplicate_keys_and_unused_surface(self):
        self.entry['AdditionalSurfaces'] = [_surface(), _surface()]
        self.entry['FaceSurfaceIndices'] = _u16s([1] * self.faces)
        errors = self._errors()
        self.assertIn('keys are not unique', errors)
        self.assertIn(f"additional surface '{NEW0}' is used by no face", errors)

    def test_indices_count_and_range(self):
        self.entry['AdditionalSurfaces'] = [_surface()]
        self.entry['FaceSurfaceIndices'] = _u16s([1, 2])
        errors = self._errors()
        self.assertIn(f'holds 2 entries for FacesCount {self.faces}', errors)
        self.assertIn('value 2 is out of range for 1 additional surface(s)', errors)
        self.entry['FaceSurfaceIndices'] = base64.b64encode(b'\x00').decode('ascii')
        self.assertIn('not a whole number of uint16', self._errors())

    def test_surfaces_without_indices_refused(self):
        self.entry['AdditionalSurfaces'] = [_surface()]
        self.assertIn('need FaceSurfaceIndices', self._errors())

    def test_entry_checks_are_the_shared_new_surface_ones(self):
        self.entry['AdditionalSurfaces'] = [
            _surface(key=NEW0, template='builtin:no_such', texture={}, SpecularStrength=300),
            _surface(key=NEW1, texture={'AdditionalTextureFileName': 'missing.png'}),
        ]
        self.entry['FaceSurfaceIndices'] = _u16s([1] * (self.faces - 1) + [2])
        errors = self._errors()
        self.assertIn(f"additional surface '{NEW0}': builtin: unknown template", errors)
        self.assertIn('exactly one of DonorTextureIndex or AdditionalTextureFileName', errors)
        self.assertIn('SpecularStrength 300', errors)
        self.assertIn("'missing.png' has no AdditionalTextureDescriptors entry", errors)

    def test_built_in_binding_more_layers_than_uv_channels_refused(self):
        # The exported cube has 2 UV channels (the Spec template); drop one.
        self.entry['UVChannels'] = self.entry['UVChannels'][:1]
        self.entry['TemplateSource'] = 'rigid:' + DONOR_SURFACE
        self.entry['AdditionalSurfaces'] = [_surface()]
        self.entry['FaceSurfaceIndices'] = _u16s([1] * self.faces)
        errors = self._errors()
        self.assertIn('binds 2 texture layer(s) on this model but the host submesh has only 1', errors)


class ParseTests(_DonorCase):
    def test_parse_reads_surfaces_and_indices(self):
        self.entry['AdditionalSurfaces'] = [_surface(SpecularStrength=20)]
        self.entry['FaceSurfaceIndices'] = _u16s([0, 1] * (self.faces // 2))
        cs = main.ParseSluggie(self.data).custom_submeshes[0]
        self.assertEqual([s.surface_key for s in cs.additional_surfaces], [NEW0])
        self.assertEqual(cs.additional_surfaces[0].material_name, f'mat_{NEW0}')
        self.assertEqual(cs.additional_surfaces[0].texture_assignment.donor_texture_index, 1)
        self.assertEqual(cs.additional_surfaces[0].specular_strength, 20)
        self.assertEqual(cs.face_surface_indices, tuple([0, 1] * (self.faces // 2)))

    def test_parse_defaults_without_the_fields(self):
        cs = main.ParseSluggie(self.data).custom_submeshes[0]
        self.assertEqual(cs.additional_surfaces, [])
        self.assertEqual(cs.face_surface_indices, ())


class BuildTests(_DonorCase):
    def _custom_blob(self, gpl):
        return trr._blob(gpl, len(self.model['Submeshes']))

    def _faces_of(self, blob, record_index):
        return trr._record_faces(blob, record_index)

    def _type7_records(self, blob):
        return [k for k, record in enumerate(blob['records']) if record[0] == 7]

    def test_single_surface_output_is_unchanged_by_the_phase(self):
        _result, gpl = self._build()
        blob = self._custom_blob(gpl)
        self.assertEqual(self._type7_records(blob), [5])
        self.assertEqual(len(self._faces_of(blob, 5)), self.faces)

    def test_two_surfaces_split_the_faces_and_share_the_layout(self):
        split = [0] * (self.faces // 2) + [1] * (self.faces - self.faces // 2)
        self.entry['AdditionalSurfaces'] = [_surface(template='rigid:' + DONOR_SURFACE, SpecularStrength=77)]
        self.entry['FaceSurfaceIndices'] = _u16s(split)
        _result, gpl = self._build()
        blob = self._custom_blob(gpl)

        drawing = self._type7_records(blob)
        self.assertEqual(len(drawing), 2)
        primary, appended = drawing
        self.assertEqual(primary, 5)
        self.assertEqual(appended, len(blob['records']) - 1)
        # The appended group is canonical: T1 L0, T1 L1, T4, T3, T6, T7.
        self.assertEqual([r[0] for r in blob['records'][6:]], [1, 1, 4, 3, 6, 7])
        # Faces went where the indices said, in order.
        all_faces = self._faces_of(blob, primary) + self._faces_of(blob, appended)
        self.assertEqual(len(self._faces_of(blob, primary)), split.count(0))
        self.assertEqual(len(self._faces_of(blob, appended)), split.count(1))
        self.assertEqual(sorted(all_faces), sorted(self._faces_of(trr._blob(trr._gpl_section(self._build_single()), len(self.model['Submeshes'])), 5)))
        # Same attribute layout on both surfaces, so they index the same arrays.
        self.assertEqual(trr._type3(blob, appended), trr._type3(blob, primary))
        # Layer 0 of the new surface binds donor texture 1; its specular byte is 77.
        self.assertEqual(blob['records'][6][4:8], bytes.fromhex('11110001'))
        self.assertEqual(blob['records'][appended][1], 77)

    def _build_single(self):
        data = self.env.reload()
        data['SluggiesModel']['UseHammerspace'] = True
        entry = dict(self.entry)
        entry.pop('AdditionalSurfaces', None)
        entry.pop('FaceSurfaceIndices', None)
        data['SluggiesModel']['CustomSubmeshes'] = [entry]
        modes = main.SectionModes(gpl='build', act='clone', tex='clone', skn='clone', trailing='clone')
        return main.BuildModelBlock(data, modes, sluggie_path=self.env.sluggie_path).block

    def test_three_surfaces_each_get_their_faces(self):
        choice = [k % 3 for k in range(self.faces)]
        self.entry['AdditionalSurfaces'] = [
            _surface(key=NEW0, template='derived:sm0_ds5'),
            _surface(key=NEW1, template='builtin:rigid_spec_v1', texture={'DonorTextureIndex': 0}),
        ]
        self.entry['FaceSurfaceIndices'] = _u16s(choice)
        _result, gpl = self._build()
        blob = self._custom_blob(gpl)
        drawing = self._type7_records(blob)
        self.assertEqual(len(drawing), 3)
        for k, record in enumerate(drawing):
            self.assertEqual(len(self._faces_of(blob, record)), choice.count(k))

    def test_primary_may_end_up_with_no_faces(self):
        self.entry['AdditionalSurfaces'] = [_surface()]
        self.entry['FaceSurfaceIndices'] = _u16s([1] * self.faces)
        _result, gpl = self._build()
        blob = self._custom_blob(gpl)
        self.assertEqual(blob['lists'][5], b'')
        self.assertEqual(len(self._faces_of(blob, len(blob['records']) - 1)), self.faces)

    def test_additional_surface_on_an_appended_texture(self):
        from PIL import Image
        Image.new('RGBA', (4, 4), (0, 255, 0, 255)).save(self.env.directory / 'tex' / 'new.png')
        self.model.update({
            'ReimportTextures': True,
            'AdditionalTextureDescriptors': [{'TextureFileName': 'new.png', 'TemplateTextureIndex': 0}],
        })
        self.entry['AdditionalSurfaces'] = [_surface(texture={'AdditionalTextureFileName': 'new.png'})]
        self.entry['FaceSurfaceIndices'] = _u16s([0] * (self.faces - 2) + [1, 1])

        with self.assertRaisesRegex(ValueError, 'CustomSubmeshes with TextureAssignment.AdditionalTextureFileName require'):
            main.BuildModelBlock(self.data, main.SectionModes(gpl='build'), sluggie_path=self.env.sluggie_path)

        plan = texture_helper.TexturePlan(entries=(texture_helper.TexturePlanEntry(
            texture_index=synthetic_donor.TEXTURE_COUNT, texture_file_name='new.png',
            width=4, height=4, format=synthetic_donor.TEXTURE_FORMAT, format_name='CMPR',
            image_data=bytes([0x22] * synthetic_donor.TEXTURE_PAYLOAD_LENGTH), palette_data=b'',
            palette_entries=0, palette_format=None, template_texture_index=0,
        ),))
        with mock.patch.object(texture_helper, 'build_hammerspace_texture_plan', return_value=plan):
            result, gpl = self._build(tex='build')
        blob = self._custom_blob(gpl)
        self.assertEqual(blob['records'][6][4:8], bytes.fromhex(f'1111000{synthetic_donor.TEXTURE_COUNT}'))
        self.assertEqual(result.validation_report['validator_facts']['tex_texture_count'], synthetic_donor.TEXTURE_COUNT + 1)


class TextureNameConsumersTests(unittest.TestCase):
    def test_dispatcher_selects_tex_build_for_an_additional_surface_png(self):
        model = {
            'CustomSubmeshes': [{
                'TextureAssignment': {'DonorTextureIndex': 0},
                'AdditionalSurfaces': [_surface(texture={'AdditionalTextureFileName': 'new.png'})],
            }],
        }
        args = start.hammerspace_section_args(model)
        self.assertEqual(args, ['--gpl', 'build', '--tex', 'build'])
        model['CustomSubmeshes'][0]['AdditionalSurfaces'][0]['TextureAssignment'] = {'DonorTextureIndex': 1}
        self.assertEqual(start.hammerspace_section_args(model), ['--gpl', 'build'])

    def test_merge_remaps_an_additional_surface_to_the_kept_texture(self):
        model = {
            'TextureDescriptors': [{'TextureIndex': 0, 'TextureFileName': '0.png'}],
            'AdditionalTextureDescriptors': [
                {'TextureFileName': 'a.png', 'TemplateTextureIndex': 0},
                {'TextureFileName': 'b.png', 'TemplateTextureIndex': 0},
            ],
            'CustomSubmeshes': [{
                'TextureAssignment': {'AdditionalTextureFileName': 'b.png'},
                'AdditionalSurfaces': [_surface(texture={'AdditionalTextureFileName': 'b.png'})],
            }],
        }
        with mock.patch.object(texture_helper, 'find_duplicate_additional_textures', return_value=[0, 0]):
            main._merge_duplicate_texture_additions(model, 'x.sluggie')
        cs = model['CustomSubmeshes'][0]
        self.assertEqual(cs['TextureAssignment']['AdditionalTextureFileName'], 'a.png')
        self.assertEqual(cs['AdditionalSurfaces'][0]['TextureAssignment']['AdditionalTextureFileName'], 'a.png')
        self.assertEqual(len(model['AdditionalTextureDescriptors']), 1)


# --- exporter side (bpy-free) ----------------------------------------------------

def _tri(polygon_index):
    return cse.Triangle(vertices=(0, 1, 2), loops=(0, 1, 2), polygon_index=polygon_index)


def _slot(name, surface_id, new=False, owner=None):
    return rre.SlotSurface(material_name=name, surface_id=surface_id, new_surface=new, owner=owner)


class RoutingHelperTests(unittest.TestCase):
    def test_new_surface_key_matches_owner(self):
        self.assertTrue(rre.new_surface_key_matches('sm1', 'sm1_new0'))
        self.assertTrue(rre.new_surface_key_matches(CS_ID, NEW1))
        self.assertFalse(rre.new_surface_key_matches('sm1', 'sm12_new0'))
        self.assertFalse(rre.new_surface_key_matches(CS_ID, 'sm1_new0'))
        self.assertFalse(rre.new_surface_key_matches(CS_ID, None))

    def test_routes_faces_to_primary_and_additional_surfaces(self):
        slots = [_slot('prim', PRIMARY), _slot('a', NEW0, True, CS_ID), _slot('b', NEW1, True, CS_ID)]
        triangles = [_tri(0), _tri(1), _tri(2), _tri(1), _tri(0)]
        routing = rre.route_custom_submesh_faces('Cube', CS_ID, PRIMARY, triangles, [2, 1, 0], slots)
        self.assertEqual(routing.table, [NEW1, NEW0, PRIMARY])
        self.assertEqual(routing.new_keys, [NEW1, NEW0])
        self.assertTrue(routing.changed)
        self.assertEqual(rre.custom_submesh_face_surface_indices(routing), [1, 2, 0, 2, 1])
        self.assertEqual(routing.counts_after, {NEW1: 2, NEW0: 2, PRIMARY: 1})
        self.assertIn('Cube: faces per surface', rre.surface_report('Cube', routing))

    def test_primary_only_gives_no_new_keys(self):
        routing = rre.route_custom_submesh_faces('Cube', CS_ID, PRIMARY, [_tri(0), _tri(0)], [0, 0], [_slot('prim', PRIMARY)])
        self.assertEqual(routing.new_keys, [])
        self.assertEqual(rre.custom_submesh_face_surface_indices(routing), [0, 0])

    def test_foreign_or_empty_slots_cancel(self):
        slots = [_slot('prim', PRIMARY), _slot('cap', 'sm1_new0', True, 'sm1'), None, _slot('donor', 'sm1_ds5')]
        for slot_index, fragment in ((1, 'belongs to sm1'), (2, 'empty material slot'), (3, 'not a surface of this submesh')):
            with self.assertRaisesRegex(ValueError, fragment):
                rre.route_custom_submesh_faces('Cube', CS_ID, PRIMARY, [_tri(0)], [slot_index], slots)

    def test_merged_slots_warn(self):
        slots = [_slot('prim', PRIMARY), _slot('a', NEW0, True, CS_ID), _slot('a2', NEW0, True, CS_ID)]
        routing = rre.route_custom_submesh_faces('Cube', CS_ID, PRIMARY, [_tri(0), _tri(1)], [1, 2], slots)
        self.assertEqual(routing.new_keys, [NEW0])
        self.assertEqual(len(routing.warnings), 1)
        self.assertIn('merged', routing.warnings[0])


class EntryBuilderTests(unittest.TestCase):
    def _entry(self, **kwargs):
        geometry = tcs._cube_geometry()
        normals, uvs, colors = tcs._cube_loop_attributes()
        model = synthetic_donor.build_sluggie()['SluggiesModel']
        plan = cse.attribute_plan(model, 'builtin:rigid_spec_v1')
        return cse.build_custom_submesh_entry(
            'Cube', CS_ID, 5, 'builtin:rigid_spec_v1', plan, geometry, normals, uvs, colors,
            {'DonorTextureIndex': 0}, **kwargs,
        )

    def test_fields_written_only_with_additional_surfaces(self):
        plain = self._entry()
        self.assertNotIn('AdditionalSurfaces', plain)
        self.assertNotIn('FaceSurfaceIndices', plain)
        faces = plain['FacesCount']
        entry = self._entry(additional_surfaces=[_surface()], face_surface_indices=[1] * faces)
        self.assertEqual(entry['AdditionalSurfaces'], [_surface()])
        self.assertEqual(struct.unpack(f'>{faces}H', cse.FieldCodec.decode_field(entry['FaceSurfaceIndices'])), (1,) * faces)
        self.assertNotIn('AdditionalSurfaces', self._entry(additional_surfaces=[], face_surface_indices=[0] * faces))

    def test_index_count_and_range_checked(self):
        faces = self._entry()['FacesCount']
        with self.assertRaisesRegex(ValueError, 'face surface indices'):
            self._entry(additional_surfaces=[_surface()], face_surface_indices=[1] * (faces - 1))
        with self.assertRaisesRegex(ValueError, 'out of range'):
            self._entry(additional_surfaces=[_surface()], face_surface_indices=[2] * faces)


class ExporterGlueTests(unittest.TestCase):
    def setUp(self):
        self.helpers = tre._load(tre.EXPORTER_PATH, {
            '_find_new_materials', '_custom_submesh_additional_materials', '_new_surface_entry',
            'NEW_SURFACE_BODY_MESSAGE',
        }, {'struct': struct, '_to_bytes': base64.b64decode, 'RigidRebuildExport': rre})

    def test_find_new_materials_accepts_own_add_material_surfaces(self):
        find = self.helpers['_find_new_materials']
        prim = tre._Mat('prim', SurfaceId=PRIMARY)
        own = tre._Mat('own', SurfaceId=NEW0, SluggiesNewSurface=True, SluggiesSurfaceOwner=CS_ID)
        foreign = tre._Mat('cap', SurfaceId='sm1_new0', SluggiesNewSurface=True, SluggiesSurfaceOwner='sm1')
        donor = tre._Mat('donor', SurfaceId='sm1_ds5')
        obj = tre._obj([prim, own], custom=True)
        obj.get = {'SluggiesCustomSubmesh': True, 'CustomSubmeshId': CS_ID}.get
        self.assertEqual(find(obj, None), [])
        obj = tre._obj([prim, foreign, donor], custom=True)
        obj.get = {'SluggiesCustomSubmesh': True, 'CustomSubmeshId': CS_ID}.get
        self.assertEqual(find(obj, None), [
            ('cap', f'belongs to sm1, not {CS_ID}'),
            ('donor', "SurfaceId 'sm1_ds5' is not this custom submesh's own surface"),
        ])

    def test_additional_materials_are_own_used_new_surfaces(self):
        collect = self.helpers['_custom_submesh_additional_materials']
        prim = tre._Mat('prim', SurfaceId=PRIMARY)
        used = tre._Mat('used', SurfaceId=NEW0, SluggiesNewSurface=True, SluggiesSurfaceOwner=CS_ID)
        unused = tre._Mat('unused', SurfaceId=NEW1, SluggiesNewSurface=True, SluggiesSurfaceOwner=CS_ID)
        foreign = tre._Mat('cap', SurfaceId='sm1_new0', SluggiesNewSurface=True, SluggiesSurfaceOwner='sm1')
        obj = SimpleNamespace(
            name='Cube',
            get={'CustomSubmeshId': CS_ID}.get,
            material_slots=[SimpleNamespace(material=m) for m in (prim, used, unused, foreign)],
            data=SimpleNamespace(polygons=[SimpleNamespace(material_index=i) for i in (0, 1, 3)]),
        )
        materials, unused_names = collect(obj)
        self.assertEqual(list(materials), [NEW0])
        self.assertIs(materials[NEW0], used)
        self.assertEqual(unused_names, ['unused'])

    def test_new_surface_entry_shape(self):
        entry = self.helpers['_new_surface_entry']
        mat = tre._Mat('m', TemplateSource='builtin:rigid_spec_v1', SpecularStrength=999)
        self.assertEqual(entry(NEW0, mat, {'DonorTextureIndex': 2}), {
            'SurfaceKey': NEW0, 'MaterialName': 'm', 'TemplateSource': 'builtin:rigid_spec_v1',
            'TextureAssignment': {'DonorTextureIndex': 2}, 'SpecularStrength': 255,
        })
        self.assertNotIn('SpecularStrength', entry(NEW0, tre._Mat('m'), {'DonorTextureIndex': 2}))

    def test_execute_resolves_additional_materials_with_the_primary_ones(self):
        source = tre.EXPORTER_PATH.read_text(encoding='utf-8')
        execute = source[source.index('custom_texture_entries = []'):source.index('# --- Rigid rebuilds')]
        self.assertIn('_custom_submesh_additional_materials(obj)', execute)
        self.assertLess(execute.index('_custom_submesh_additional_materials(obj)'),
                        execute.index('_resolve_custom_submesh_texture_changes('))
        self.assertIn('texture_assignments=custom_assignments', execute)
        # The rigid-rebuild entry builder shares the surface-entry shape.
        self.assertIn('_new_surface_entry(key, mat, texture_assignments[mat.name])', source)


if __name__ == '__main__':
    unittest.main()
