"""Find working files in 2_Output_Models by their bare file name.

The patcher accepts a bare name (``78277664_mario.gpl.sluggie``, ``tex1_..._14.png``) and searches the models
folder for it. A name that exists more than once (a copied model folder, or a block the export writes for two
directories) has no single target, so the lookup refuses it instead of taking the first file the walk finds.
Dependency-free: ``start.py`` and ``texture_helper`` both use it.
"""

import os


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
