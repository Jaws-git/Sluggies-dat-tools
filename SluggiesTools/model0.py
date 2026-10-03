from tpl import *
from gpl import *
from act import *
from anm import *
import numpy as np
import os
import shutil
import slogger
import glb_export

_LOG_DIR_INDEX = None
# (output dir, geo name) -> (model folder name, {texture index: png name}) of every
# model exported with textures. An L_ model has no TEX section and binds its
# high-poly partner's (same folder, geo name without "L_", exported just before it).
_GLB_PARTNER_TEXTURES = {}
TEX_TEMP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '2_Output_Models', 'tex_temp')
)

UNTANGLE_SKIP_STADIUMS = True
UNTANGLE_IGNORE_RAW = { 'tex1_64x64_d6da4880cee95b7b_14' , "tex1_64x64_a09662ae19841ea4_14" , "tex1_64x64_8a05f75d65053b44_14", "tex1_64x64_cc3d32b121549b17_14", "tex1_256x1024_bbd4db6290a9ce03_14", "tex1_256x512_0cd3d3567193d20d_14", "tex1_512x512_82ae50a699739e73_14", "tex1_64x64_5f51e327c244f28a_14" }

# Entries may be written with or without the .png extension; compare on basenames only.
UNTANGLE_IGNORE_BASENAME = {
    name[:-4] if name.lower().endswith('.png') else name
    for name in UNTANGLE_IGNORE_RAW
}



def _copy_texture_pngs(output_dir):
    tex_dir = os.path.join(output_dir, 'tex')
    os.makedirs(tex_dir, exist_ok=True)
    for filename in os.listdir(tex_dir):
        if filename.lower().endswith('.png'):
            os.remove(os.path.join(tex_dir, filename))
    for filename in os.listdir(TEX_TEMP_DIR):
        shutil.copyfile(
            os.path.join(TEX_TEMP_DIR, filename),
            os.path.join(tex_dir, filename),
        )


class ExpectedFormatSkip(Exception):
    pass


class UnexportableEntrySkip(ExpectedFormatSkip):
    pass


def set_log_dir_index(dir_index):
    global _LOG_DIR_INDEX
    _LOG_DIR_INDEX = dir_index


def _log_prefix():
    if _LOG_DIR_INDEX is None:
        return '[dir ?]'
    return f'[dir {_LOG_DIR_INDEX}]'


def _log_noncritical(message, exc=None):
    prefix = _log_prefix()
    if exc is None:
        slogger.warning(f'{prefix} {message}', source='model')
        return
    slogger.warning(f'{prefix} {message}: {type(exc).__name__}: {exc}', source='model')

class MaybeArchive(FileChunk):
    def analyze(self):
        word1 = self.word()
        self.child = None
        # print(hex(word1))
        if word1 in (0x01321AFD, 0x013240DB, 0x01324210):
            self.child = self.add_child(0, self.length, ANM)
            return
        elif word1 > 1000:
            return
        elif word1 > 0:
            self.child = self.add_child(0, self.length, Archive)
        else:
            self.child = self.add_child(0, self.length, Model0)

class Archive(FileChunk):
    def analyze(self):
        self.fileCount = self.word()
        offsets = [self.word() for _ in range(self.fileCount)]
        populated = [
            (slot_index, offset)
            for slot_index, offset in enumerate(offsets)
            if offset != 0
        ]
        for index in range(1, len(populated)):
            if populated[index][1] < populated[index - 1][1]:
                raise ExpectedFormatSkip('Skipping entry: unsupported archive layout')
        ends = [entry[1] for entry in populated[1:]] + [self.length]
        self.files = [None] * self.fileCount
        for (slot_index, offset), end in zip(populated, ends):
            self.files[slot_index] = self.add_child(offset, end - offset, Model0)
        self.success = []
        for slot_index, _ in populated:
            file = self.files[slot_index]
            try:
                file.analyze()
                self.success.append(slot_index)
            except UnexportableEntrySkip as exc:
                slogger.info(f'{_log_prefix()} {exc}', source='model')
            except ExpectedFormatSkip as exc:
                slogger.info(f'{_log_prefix()} {exc}', source='model')
            except Exception as e:
                _log_noncritical('failed analyzing in archive', e)
                pass

    def toFile(self, outdir, export_tex=True, export_glb=False, untangle_context=None):
        if not len(self.success):
            return
        archivedir = outdir + str(self.absolute) + '/'
        if not os.path.exists(archivedir):
            os.mkdir(archivedir)
        for success in self.success:
            f = self.files[success]
            f.toFile(archivedir, export_tex=export_tex, export_glb=export_glb, untangle_context=untangle_context)

# This is not the mdl0 format, I think I just called the class that for some reason
class Model0(FileChunk):
    def analyze(self):
        # Initial testing if this is a valid file
        firstWord = self.word()
        if firstWord != 0:
            raise ExpectedFormatSkip('Skipping archive member: not a model entry')
        self.gplPtr = self.word()
        if self.gplPtr == 0:
            raise UnexportableEntrySkip('Skipping sub-entry: empty reserved equipment slot')
        if self.gplPtr > 0x40 or self.gplPtr < 0x20:
            raise Exception("no mesh data (gpl pointer is " + hex(self.gplPtr) + ")")
        self.ptr3 = self.word()
        self.texPtr = self.word()
        self.ptr5 = self.word()
        self.ptr6 = self.word()
        self.ptr7 = self.word()
        self.ptr8 = self.word()
        positions = [self.gplPtr, self.ptr3, self.texPtr, self.ptr5, self.ptr6, self.ptr7, self.ptr8]
        positions.sort()
        nextSection = {}
        self.ACT = None
        self.GPL = None
        self.TEXPalette = None
        self.SKN = None
        for i in range(0, len(positions) - 1):
            nextSection[positions[i]] = positions[i + 1]
        nextSection[positions[-1]] = self.length
        if self.ptr3:
            self.ACT = self.add_child(self.ptr3, nextSection[self.ptr3] - self.ptr3, ACTLayout, "ACT Layout")
            self.ACT.analyze()
        if self.gplPtr:
            self.GPL = self.add_child(self.gplPtr, nextSection[self.gplPtr] - self.gplPtr, GPL, "GPL")
            self.GPL.analyze()
        if self.texPtr:
            self.TEXPalette = self.add_child(self.texPtr, nextSection[self.texPtr] - self.texPtr, TEXPalette, "TPL")
            self.TEXPalette.analyze()
        # print(hex(self.absolute))
        # print(self.ptr5)
        if self.ptr5:
            self.SKN = self.add_child(self.ptr5, nextSection[self.ptr5] - self.ptr5, SKN, "Skin")
            self.SKN.analyze()
        self.name = str(self.absolute)
        self.bones = {}
        self.skinned = 0
        if self.ACT:
            self.name += '_' + self.ACT.geoName
            self.generateBones()
        elif self.GPL:
            self.name += '_' + self.GPL.geoDescriptors[0].layout.DOTextureDataHeaders[0].paletteName

    def generateBones(self):
        self.bones = self.ACT.bones()
        # At this point we have the list of bones, time to get the influences for each
        self.bone_influences = self.boneInfluences()
        vertex_weight_totals = {}
        for influence in self.bone_influences:
            self.bones[influence.boneID].addInfluence(influence)
        self.skinned = False
        for bone in self.bones.values():
            if bone.skinned:
                self.skinned = True
            for vertex_id in bone.vertexInfluences:
                key = str(bone.GEOID) + '_' + str(vertex_id)
                if key not in vertex_weight_totals:
                    vertex_weight_totals[key] = 0
                vertex_weight_totals[key] += bone.vertexInfluences[vertex_id][0]
        # for bone in self.bones.values():
        #     for vertex_id in bone.vertexInfluences:
        #         key = str(bone.GEOID) + '_' + str(vertex_id)
        #         bone.vertexInfluences[vertex_id][0] /= vertex_weight_totals[key]

    def boneInfluences(self):
        boneInfluences = []
        if self.SKN:
            boneInfluences = self.SKN.boneInfluences()
        for geoID in list(self.ACT.non_skinned_bones.keys()):
            if geoID > 0 or len(self.bones) == self.GPL.numDescriptors:
                geoBone = self.ACT.non_skinned_bones[geoID]
            else:
                continue
            boneInfluences += [BoneInfluence(geoBone, 256, i, (100, 100, 100), 'Non-skinned assumption') for i in range(self.GPL.geoDescriptors[geoID].layout.DOPositionHeader.numPositions)]
        return boneInfluences

    def toFile(self, outdir, export_tex=True, export_glb=False, untangle_context=None):
        try:
            file_dir = outdir + self.name
            glb_exists = os.path.exists(os.path.join(file_dir, self.name + '.glb'))
            if not export_tex and glb_exists:
                return
            if os.path.exists(file_dir):
                # Selectively remove only export-produced files so that unrelated
                # files placed in the output folder are not destroyed.
                _export_exts = {'.sluggie'}
                if export_glb:
                    # .dae: left over from the retired Collada export
                    _export_exts |= {'.glb', '.dae', '.png'}
                for _fname in os.listdir(file_dir):
                    _fpath = os.path.join(file_dir, _fname)
                    if os.path.isfile(_fpath) and os.path.splitext(_fname)[1].lower() in _export_exts:
                        os.remove(_fpath)
                    elif export_glb and os.path.isdir(_fpath) and _fname == 'tex':
                        shutil.rmtree(_fpath)
            else:
                os.mkdir(file_dir)
            file_dir += '/'
            # model_data() still runs when glb export is off: untangling side effects
            # (rewriting texture bytes into the dat and recording name overrides) happen here.
            data = self.model_data(
                export_tex=export_tex,
                untangle_context=untangle_context,
                texture_output_dir=file_dir if export_tex else None,
            )
            geo_name = self.ACT.geoName if self.ACT else None
            if export_glb:
                partner = None
                if self.TEXPalette is None and geo_name and geo_name.startswith('L_'):
                    partner = _GLB_PARTNER_TEXTURES.get((outdir, geo_name[2:]))
                glb_export.write_glb(data, file_dir, self.name, partner=partner)
            if geo_name and data.textures:
                _GLB_PARTNER_TEXTURES[(outdir, geo_name)] = (self.name, dict(data.textures))
        except Exception as e:
            _log_noncritical(f'failed exporting model {self.name}', e)

    def model_data(self, export_tex=True, untangle_context=None, texture_output_dir=None):
        all_bones = []
        texture_paths = {}

        # textures are simple enough
        if export_tex and self.TEXPalette:
            if os.path.exists(TEX_TEMP_DIR):
                shutil.rmtree(TEX_TEMP_DIR)
            os.makedirs(TEX_TEMP_DIR)
            for tex_ind, tex in enumerate(self.TEXPalette.descriptors):
                image_override = None
                tlut_override = None
                dolphin_name = tex.dolphinTextureBasename()
                image_abs = tex.parent.absolute + tex.dataPtr

                if untangle_context and untangle_context.get('enabled'):
                    # //quick hack to prevent destruction of this file that would get "untangled" 395 times in total, destroying much of the picture
                    if dolphin_name in UNTANGLE_IGNORE_BASENAME:
                        pass
                    else:
                        seen_names = untangle_context.setdefault('seen_names', set())
                        seen_image_starts = untangle_context.setdefault('seen_image_starts', {})
                        known_starts = seen_image_starts.setdefault(dolphin_name, set())
                        max_attempts = untangle_context.get('max_attempts', 8192)
                        if image_abs in known_starts:
                            # Same hash/name and same source image start: do not untangle again.
                            seen_names.add(dolphin_name)
                            report = untangle_context.setdefault('report_lines', [])
                            report.append(
                                f'Texture file untangle skipped (known address): {dolphin_name}.png '
                                f'(model 0x{self.absolute:x}, tex {tex_ind}, image_start 0x{image_abs:x})'
                            )
                        else:
                            untangled = tex.ensureUniqueDolphinBasename(seen_names, max_attempts=max_attempts)
                            dolphin_name = untangled['basename']
                            known_starts.add(image_abs)
                            if untangled['changed']:
                                image_override = untangled['image_data']
                                tlut_override = untangled['tlut_data']
                                dat_out = untangle_context.get('dat_output_handle')
                                if dat_out:
                                    dat_out.seek(image_abs)
                                    dat_out.write(image_override)
                                report = untangle_context.setdefault('report_lines', [])
                                report.append(
                                    f'Texture file untangled: {untangled["original_basename"]}.png -> {dolphin_name}.png '
                                    f'(model 0x{self.absolute:x}, tex {tex_ind}, image_start 0x{image_abs:x}, attempts {untangled["attempts"]})'
                                )
                            elif untangled['warning']:
                                warnings = untangle_context.setdefault('warnings', [])
                                warnings.append(
                                    f'Warning: {untangled["warning"]} (model 0x{self.absolute:x}, tex {tex_ind}, '
                                    f'image_start 0x{image_abs:x}, basename {untangled["original_basename"]}.png)'
                                )

                if untangle_context and untangle_context.get('enabled'):
                    name_overrides = untangle_context.setdefault('name_overrides', {})
                    model_overrides = name_overrides.setdefault(self.absolute, {})
                    model_overrides[tex_ind] = dolphin_name

                dolphin_path = os.path.join(TEX_TEMP_DIR, dolphin_name)
                if not os.path.exists(dolphin_path + '.png'):
                    tex.toFile(dolphin_path, image_data_override=image_override, tlut_data_override=tlut_override)
                texture_paths[tex_ind] = dolphin_name + '.png'

            if texture_output_dir:
                _copy_texture_pngs(texture_output_dir)

        geometries = []
        vertex_deletions = {}
        # vertex data/triangles are a little more complicated because of the multiple geodescriptors
        for descriptor_ind, descriptor in enumerate(self.GPL.geoDescriptors):
            vertex_deletions[descriptor_ind] = {0: 0}
            geometry = Object()
            layout = descriptor.layout
            # Full rows: a skinned submesh interleaves its normals after each position.
            coords = np.array(layout.DOPositionHeader.data)

            if self.skinned:
                deletion_count = 0
                vertex_ind = 0
                while vertex_ind < len(coords):
                    if coords[vertex_ind][0] != 0 or coords[vertex_ind][1] != 0 or coords[vertex_ind][2] != 0:
                        vertex_ind += 1
                        continue
                    include = 0
                    for bone in self.bones.values():
                        influences = bone.vertexInfluences
                        if vertex_ind + deletion_count in influences and bone.GEOID == descriptor_ind:
                            include = 1
                            break
                    if include:
                        vertex_ind += 1
                    else:
                        coords = np.delete(coords, vertex_ind, 0)
                        deletion_count += 1
                        vertex_deletions[descriptor_ind][vertex_ind + deletion_count] = deletion_count
            # if descriptor_ind == 0:
            #     print(vertex_deletions)
            #     print(prior_deletions(vertex_deletions[0], 120))

            geometry.position_normals = [coord[3:6] for coord in coords] if coords.ndim == 2 and coords.shape[1] >= 6 else None
            coords = [coord[:3] for coord in coords]

            normals = np.array(layout.DOLightingHeader.data)
            normals = [normal[:3] for normal in normals]
            colors = np.array(layout.DOColorHeader.data)
            tex_coords = [np.array(tex_layer.data) for tex_layer in layout.DOTextureDataHeaders]

            geometry.positions = coords
            geometry.normals = normals
            geometry.colors = colors
            geometry.tex_coords = tex_coords
            geometry.meshes = []
            meshes = layout.getTriangles()
            for mesh in meshes:
                state = mesh['state']
                triangles = mesh['triangles']
                if len(triangles) == 0:
                    continue
                active_descriptors = [descriptor['key'] for descriptor in state['descriptors']]
                if 'color1' in active_descriptors:
                    slogger.info('model uses color1', source='model')
                # first, get a list of the textures used by this mesh
                # a hash that maps a texture layer (0-7) to its texture id (index in the texture_paths arr)
                active_textures = {}
                for tex_layer_ind in range(8):
                    tex_layer_name = 'texture'+str(tex_layer_ind)
                    if tex_layer_name in active_descriptors:
                        active_textures[tex_layer_ind] = state[tex_layer_name]['index']

                # then, convert the triangle list to a format we want
                # probably ought to do this in the getTriangles method but I wrote that a while ago and don't wanna
                mesh_triangles = []
                for triangle in triangles:
                    mesh_triangle = []
                    for vertex in triangle:
                        mesh_vertex = Object()
                        mesh_vertex.position = vertex['position'] - prior_deletions(vertex_deletions[descriptor_ind], vertex['position'])
                        # if mesh_vertex.position < 0 or mesh_vertex.position >= len(geometry.positions):
                        #     print(mesh_vertex.position)
                        #     print(len(geometry.positions))
                        #     print('------------')
                        if 'lighting' in vertex:
                            mesh_vertex.lighting = vertex['lighting']
                        mesh_vertex.colors = {}
                        if 'color0' in active_descriptors:
                            mesh_vertex.colors[0] = vertex['color0']
                        if 'color1' in active_descriptors:
                            mesh_vertex.colors[1] = vertex['color1']
                        mesh_vertex.tex_coords = {}
                        for texture_layer in active_textures:
                            mesh_vertex.tex_coords[texture_layer] = vertex['texture' + str(texture_layer)]
                        mesh_triangle.append(mesh_vertex)
                    mesh_triangles.append(mesh_triangle)

                mesh = Object()
                mesh.triangles = mesh_triangles
                mesh.texture_layers = active_textures
                mesh.active_descriptors = active_descriptors
                geometry.meshes.append(mesh)
            geometries.append(geometry)

        all_bones = []
        # Bones are simpler
        for bone_id in self.bones:
            bone = self.bones[bone_id]
            bone_copy = Object()
            bone_copy.id = bone_id
            bone_copy.parent = -1
            if bone.parent:
                bone_copy.parent = bone.parent.id
            bone_copy.relative = bone.relative
            bone_copy.position = bone.head()
            bone_copy.pose = bone.orientation
            bone_copy.absolute_transform = bone_copy.pose.transform
            parent = bone.parent
            while parent:
                bone_copy.absolute_transform = np.matmul(bone_copy.absolute_transform, parent.orientation.transform)
                parent = parent.parent
            bone_copy.track_id = bone.track_id
            bone_copy.GEOID = bone.GEOID
            bone_copy.skinned = bone.skinned
            bone_copy.influences = {}
            for vertex_id in bone.vertexInfluences:
                new_vertex_ind = vertex_id - prior_deletions(vertex_deletions[bone.GEOID], vertex_id)
                bone_copy.influences[new_vertex_ind] = bone.vertexInfluences[vertex_id][0]
                # if new_vertex_ind < 0 or new_vertex_ind >= len(geometries[bone.GEOID].positions):
                #     print(new_vertex_ind)
                #     print(len(geometries[bone.GEOID].positions))
                #     print('------------')
            all_bones.append(bone_copy)
        # influence_counts = {}
        # influence_log = {}
        # for g_ind, geom in enumerate(geometries):
        #     for v_ind, pos in enumerate(geom.positions):
        #         # if pos[0] == 0 and pos[1] == 0 and pos[2] == 0:
        #         #     continue
        #         vert_id = str(g_ind) + '_' + str(v_ind)
        #         influence_counts[vert_id] = 0
        #         influence_log[vert_id] = {}
        #         for bone in all_bones:
        #             if bone.GEOID == g_ind:
        #                 if v_ind in bone.influences:
        #                     influence_counts[vert_id] += bone.influences[v_ind]
        #                     influence_log[vert_id][bone.id] = {'weight': bone.influences[v_ind], 'sources': self.bones[bone.id].vertexInfluences[v_ind][2]}
        # for id, val in influence_counts.items():
            # if val < 255 or val > 257:
            # if 31 in influence_log[id]:
            #     print(id)
            #     print(val)
            #     for bone, details in influence_log[id].items():
            #         print(str(bone) + ': ' + str(details['weight']) + ' (' + ', '.join(details['sources']) + ')')
            #     print('---------------')

        # return the tidy object
        return ModelData(geometries, texture_paths, all_bones, self, export_tex, untangle_context)

class ModelData():
    def __init__(self, geometries, textures, bones, model, export_tex=True, untangle_context=None):
        self.geometries = geometries
        self.textures = textures
        self.bones = bones
        self.model = model
        self.export_tex = export_tex
        self.untangle_context = untangle_context
