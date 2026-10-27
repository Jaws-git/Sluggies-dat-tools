"""The texture parts of the 'Adapt' box's Convert Materials step.

convert: every texture the models use becomes a PNG of at most
GX_MAX_TEXTURE_DIMENSION px a side (aspect kept, as the patcher would scale
it). The original is kept as `<name>_HIGH.<ext>`, in the project folder when
textures are collected, else next to the original.

collect: the textures the project's models still use are copied next to the
saved .blend, into `<blend folder>/<blend name>_Tex/`, and their images point
there with a relative path, so the .blend and its textures move together.

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

from . import ModelCheckCore


BACKUP_SUFFIX = '_HIGH'


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


def _source_path(image):
    return os.path.normpath(bpy.path.abspath(image.filepath)) if image.filepath else ''


def _is_png(image, source):
    return image.file_format == 'PNG' and source.lower().endswith('.png')


def needs_conversion(image):
    """True when *image* is a file texture that is not a PNG or is larger
    than GX_MAX_TEXTURE_DIMENSION on a side (loads the image to tell)."""
    if image.library is not None or image.source != 'FILE':
        return False
    source = _source_path(image)
    if not (source and os.path.isfile(source)) and image.packed_file is None:
        return False
    width, height = image.size
    if not (width and height):
        return False
    return not _is_png(image, source) \
        or ModelCheckCore.clamp_texture_dimensions(width, height) != (width, height)


def convert(images, folder=None):
    """Make each of *images* that needs_conversion a PNG within the GX limit
    and point it there. Its original is kept as `<name>_HIGH.<ext>`: moved
    there when it is a file in *folder* (the project folder, None when
    textures are not collected), else copied, into *folder* or next to the
    original. The PNG replaces the original when that is a PNG in the target
    folder, else goes there as `<name>.png` (a free name if taken).
    Returns (images converted, notes)."""
    converted = 0
    notes = []
    by_source = {}   # normcase source path -> its PNG
    claimed = set()  # normcase files written by this run
    for image in images:
        if not needs_conversion(image):
            continue
        source = _source_path(image)
        if os.path.normcase(source) in by_source:   # another image of the same file
            image.filepath = by_source[os.path.normcase(source)]
            image.reload()
            converted += 1
            continue
        if image.packed_file is not None:
            notes.append(f"'{image.name}' is packed: unpack it to convert it to a PNG within "
                         f"{ModelCheckCore.GX_MAX_TEXTURE_DIMENSION} px")
            continue
        target_dir = os.path.normpath(folder) if folder else os.path.dirname(source)
        stem, ext = os.path.splitext(os.path.basename(source))
        width, height = image.size
        size = ModelCheckCore.clamp_texture_dimensions(width, height)
        in_place = _is_png(image, source) \
            and os.path.normcase(os.path.dirname(source)) == os.path.normcase(target_dir)
        try:
            os.makedirs(target_dir, exist_ok=True)
            backup, there = _target(target_dir, f'{stem}{BACKUP_SUFFIX}{ext}',
                                    lambda p, s=source: filecmp.cmp(s, p, shallow=False), claimed)
            claimed.add(os.path.normcase(backup))
            if in_place:
                path = source
                if not there:
                    shutil.copy2(source, backup)
            else:
                path, _there = _target(target_dir, f'{stem}.png', None, claimed)
            if size != (width, height):
                image.scale(*size)
            image.filepath_raw = path
            image.file_format = 'PNG'
            image.save()
            if not in_place:
                # A file in the project folder is the step's own copy: it becomes the backup.
                own_copy = folder and os.path.normcase(os.path.dirname(source)) == os.path.normcase(target_dir)
                if own_copy and there:
                    os.remove(source)
                elif own_copy:
                    shutil.move(source, backup)
                elif not there:
                    shutil.copy2(source, backup)
        except (OSError, RuntimeError) as exc:
            notes.append(f"'{image.name}' could not be converted: {exc}")
            continue
        claimed.add(os.path.normcase(path))
        by_source[os.path.normcase(source)] = path
        image.filepath = bpy.path.relpath(path) if bpy.data.filepath else path
        converted += 1
        what = f"{width}x{height} -> {size[0]}x{size[1]}" if size != (width, height) else "as a PNG"
        notes.append(f"'{image.name}': {what} in {os.path.basename(path)}, "
                     f"original kept as {os.path.basename(backup)}")
    return converted, notes


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
