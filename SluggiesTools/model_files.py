"""Find working files in 2_Output_Assets by their bare file name.

The patcher accepts a bare name (``78277664_mario.gpl.sluggie``, ``tex1_..._14.png``) and searches the models
folder for it. A name that exists more than once (a copied model folder, or a block the export writes for two
directories) has no single target, so the lookup refuses it instead of taking the first file the walk finds.
Dependency-free: ``start.py`` and ``texture_helper`` both use it.

A model folder may also hold extra ``.sluggie`` files (variants saved under a new name by the Blender export, same
model identity). Code that discovers a model's file by folder prefers the exported file (named after its folder),
and pairs a High and a Low model by name first (:func:`pick_partner`). The Blender add-on keeps its own copy of
these rules (``BlenderAddonSrc/ModelCheckCore.py``).
"""

import os
import re

_OFFSET_PREFIX = re.compile(r'^\d+_')


class AmbiguousNameError(ValueError):
    """A bare file name matches several files under the search folder."""

    def __init__(self, name, matches, search_dir):
        self.name, self.matches = name, list(matches)
        lines = [f"'{name}' exists {len(self.matches)} times in "
                 f"{os.path.basename(os.path.abspath(search_dir))}; the patcher cannot tell which one you mean:"]
        lines += [f"  {_shown(path, search_dir)}" for path in self.matches]
        lines.append('Pass the full path of the file to patch, or remove the copies you do not want '
                     '(the GUI\'s Maintenance tab lists every duplicate).')
        super().__init__('\n'.join(lines))


def _shown(path, search_dir):
    try:
        return os.path.relpath(path, search_dir)
    except ValueError:                       # another drive (Windows)
        return path


def find_named(search_dir, name):
    """Every file called ``name`` under ``search_dir``, sorted."""
    return sorted(os.path.join(root, f) for root, _dirs, files in os.walk(search_dir) for f in files if f == name)


def find_unique(search_dir, name):
    """The one file called ``name`` under ``search_dir``; None when there is none. Raises
    :class:`AmbiguousNameError` when there are several."""
    matches = find_named(search_dir, name)
    if len(matches) > 1:
        raise AmbiguousNameError(name, matches, search_dir)
    return matches[0] if matches else None


def is_exported_name(path):
    """Whether a ``.sluggie`` carries the name the model export gives it: its folder's name plus ``.sluggie``."""
    folder = os.path.basename(os.path.dirname(os.path.abspath(path)))
    return os.path.normcase(os.path.basename(path)) == os.path.normcase(folder + '.sluggie')


def variant_key(path):
    """The name a High and a Low ``.sluggie`` share: the basename without ``.sluggie``, its leading block offset
    and a leading ``L_`` (``78711424_L_mario_red.gpl.sluggie`` and ``78277664_mario_red.gpl.sluggie`` both give
    ``mario_red.gpl``)."""
    name = os.path.basename(path)
    if name.lower().endswith('.sluggie'):
        name = name[:-len('.sluggie')]
    name = _OFFSET_PREFIX.sub('', name)
    if name[:2].lower() == 'l_':
        name = name[2:]
    return name.lower()


def prefer_exported(paths):
    """The exported file (:func:`is_exported_name`) among ``paths``, else the only path, else None."""
    paths = list(paths)
    exported = [p for p in paths if is_exported_name(p)]
    if len(exported) == 1:
        return exported[0]
    return paths[0] if len(paths) == 1 else None


def pick_partner(candidates, own_path):
    """The High/Low partner of ``own_path`` among ``candidates`` (``.sluggie`` files of the other role): the one
    with the same :func:`variant_key`, else the exported file, else the only candidate; None when that leaves
    several."""
    candidates = list(candidates)
    key = variant_key(own_path)
    named = [c for c in candidates if variant_key(c) == key]
    if len(named) == 1:
        return named[0]
    return prefer_exported(candidates)
