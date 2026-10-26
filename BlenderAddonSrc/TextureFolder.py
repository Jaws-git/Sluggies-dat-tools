"""The last part of the 'Adapt' box's Convert Materials step: the textures
the project's models still use are copied next to the saved .blend, into
`<blend folder>/<blend name>_Tex/`, and their images point there with a
relative path, so the .blend and its textures move together.

A copy is byte-identical to its source, so export still binds it to the same
tex/ texture (ExportSluggies._plan_external_texture_copy matches tex/ files by
name and content). Names that are taken by another file get " (1)", " (2)",
... as Windows Explorer does; a file that is already there with the same bytes
is reused, so running the step again copies nothing new.
"""

import filecmp
import os
import shutil

import bpy


def folder_for(blend_path):
    """`<blend folder>/<blend name>_Tex` for the .blend at *blend_path*."""
    stem = os.path.splitext(os.path.basename(blend_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(blend_path)), f'{stem}_Tex')


def material_images(mat):
    """The images of *mat*'s Image Texture nodes, node groups included."""
    images = []

    def walk(tree, seen):
        if tree is None or tree in seen:
            return
        seen.add(tree)
        for node in tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image is not None and node.image not in images:
                images.append(node.image)
            elif node.type == 'GROUP':
                walk(node.node_tree, seen)

    walk(mat.node_tree if mat is not None else None, set())
    return images


def _windows_names(file_name):
    """`name.png`, `name (1).png`, `name (2).png`, ..."""
    stem, ext = os.path.splitext(file_name)
    yield file_name
    n = 1
    while True:
        yield f'{stem} ({n}){ext}'
        n += 1


def _target(folder, file_name, same, claimed):
    """The path in *folder* for *file_name*: the first Windows-style name
    that is free or already holds the same bytes (*same*(path)), and that
    no other image of this run claimed. Returns (path, already there)."""
    for name in _windows_names(file_name):
        path = os.path.join(folder, name)
        if os.path.normcase(path) in claimed:
            continue
        if not os.path.exists(path):
            return path, False
        if same is not None and os.path.isfile(path) and same(path):
            return path, True


def _same_bytes(data):
    def same(path):
        try:
            with open(path, 'rb') as handle:
                return handle.read() == data
        except OSError:
            return False
    return same


def collect(images, folder):
    """Copy *images* into *folder* and point them at the copies, relative to
    the .blend. Returns (images now in *folder*, files written, notes)."""
    moved = written = 0
    notes = []
    by_source = {}   # normcase source path -> its copy
    claimed = set()  # normcase copies made or reused by this run
    for image in images:
        if image.library is not None:
            notes.append(f"'{image.name}' comes from a linked library: left where it is")
            continue
        if image.source not in ('FILE', 'GENERATED'):
            notes.append(f"'{image.name}' is a {image.source.lower()} image: left where it is")
            continue
        source = os.path.normpath(bpy.path.abspath(image.filepath)) if image.filepath else ''
        on_disk = bool(source) and os.path.isfile(source) and not image.is_dirty
        packed = image.packed_file is not None and not image.is_dirty
        if not (on_disk or packed or image.is_dirty):
            notes.append(f"'{image.name}': its file {source or '(none)'} is missing, not copied")
            continue
        if on_disk and os.path.normcase(os.path.dirname(source)) == os.path.normcase(os.path.normpath(folder)):
            claimed.add(os.path.normcase(source))
            image.filepath = bpy.path.relpath(source)
            moved += 1
            continue

        os.makedirs(folder, exist_ok=True)
        file_name = os.path.basename(source) or f'{bpy.path.clean_name(image.name)}.png'
        if on_disk and os.path.normcase(source) in by_source:
            path = by_source[os.path.normcase(source)]
        else:
            if on_disk:
                path, there = _target(folder, file_name,
                                      lambda p, s=source: filecmp.cmp(s, p, shallow=False), claimed)
                if not there:
                    shutil.copy2(source, path)
                by_source[os.path.normcase(source)] = path
            elif packed:
                data = bytes(image.packed_file.data)
                path, there = _target(folder, file_name, _same_bytes(data), claimed)
                if not there:
                    with open(path, 'wb') as handle:
                        handle.write(data)
            else:   # edited in Blender and not saved: save the edited pixels
                path, there = _target(folder, file_name, None, claimed)
                try:
                    image.save(filepath=path, save_copy=True)
                except RuntimeError as exc:
                    notes.append(f"'{image.name}' could not be saved: {exc}")
                    continue
            written += not there
        claimed.add(os.path.normcase(path))
        if image.source == 'GENERATED':
            image.source = 'FILE'
        image.filepath = bpy.path.relpath(path)
        moved += 1
    return moved, written, notes
