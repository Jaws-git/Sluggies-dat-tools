"""Portraits from a model folder: ``icon/FrontIcon.png`` + ``icon/SideIcon.png`` (+ ``SideIconAlt.png``).

The icon export (``Icons/export_icons.py``) writes each character's two
48x51 portraits into its high-poly model folder, plus the alternate side view
(the side portrait facing the other way) for a character that has one of its
own::

    2_Output_Assets/<dir> <name>/<offset>_<geo>.gpl/icon/{FrontIcon,SideIcon,SideIconAlt}.png

``find(path)`` takes a ``.sluggie`` (or its model folder) and gives the
PNGs. An ``L_`` model has no portraits of its own: its high-poly partner's
folder (a sibling folder with the same geo-name stem, holding a ``.sluggie``)
is used. When the side or the front view is missing, nothing is imported:
``problem`` says why, and the slot keeps its current portrait. The alternate
side view is optional (``side_alt`` None without it).

Roster config: ``"icon": {"model": "<.sluggie or model folder>"}`` in place
of ``side``/``front`` (``icons.parse_icons``); a relative path counts from the
repository root.
"""

import os
import re
from dataclasses import dataclass

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
ICON_SUBDIR = 'icon'                                       # export_icons.CHARACTER_ICON_DIR
ALT_VIEW = 'side_alt'                                      # export_icons.ALT_VIEW
FILES = {'side': 'SideIcon.png', 'front': 'FrontIcon.png', ALT_VIEW: 'SideIconAlt.png'}  # export_icons.CHARACTER_ICON_FILES
REQUIRED = ('side', 'front')                               # the alternate side view is optional
# <offset>_<geo>; exports made before 2026-10-04 can carry a leftover byte after '.gpl' (binfmt.clean_geo_name)
MODEL_FOLDER = re.compile(r'^\d+_(?P<low>L_)?(?P<geo>.+)$', re.IGNORECASE)


@dataclass(frozen=True)
class ModelIcons:
    home: str | None            # the high-poly model folder the portraits come from
    side: str | None            # path of SideIcon.png (None: missing)
    front: str | None
    problem: str | None = None  # why nothing is imported (None: both views found)
    side_alt: str | None = None  # path of SideIconAlt.png (None: the model has no alternate side view)

    @property
    def ok(self) -> bool:
        return self.problem is None


def _stem(geo: str) -> str:
    return geo.split('.', 1)[0].lower()


def _has_sluggie(folder: str) -> bool:
    try:
        return any(name.lower().endswith('.sluggie') for name in os.listdir(folder))
    except OSError:
        return False


def high_poly_folder(model_folder: str) -> tuple[str | None, str | None]:
    """``(folder, None)``: the high-poly folder of ``model_folder`` (itself, or an ``L_`` model's partner), or
    ``(None, reason)``."""
    name = os.path.basename(os.path.normpath(model_folder))
    m = MODEL_FOLDER.match(name)
    if m is None:
        return None, f'{name} is not a model folder (<offset>_<geo name>)'
    if not m.group('low'):
        return model_folder, None
    parent = os.path.dirname(os.path.normpath(model_folder))
    stem = _stem(m.group('geo'))
    partners = []
    for sibling in sorted(os.listdir(parent)):
        sm = MODEL_FOLDER.match(sibling)
        path = os.path.join(parent, sibling)
        if sm and not sm.group('low') and _stem(sm.group('geo')) == stem and os.path.isdir(path) \
                and _has_sluggie(path):
            partners.append(path)
    if not partners:
        return None, f'{name} has no High partner folder beside it (its portraits live there)'
    if len(partners) > 1:
        return None, f'{name} has several High partner folders:' + ', '.join(
            os.path.basename(p) for p in partners)
    return partners[0], None


def resolve_path(path: str) -> str:
    """``path`` as an absolute path (a relative one counts from the repository root)."""
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(ROOT, path))


def find(path: str) -> ModelIcons:
    """The portraits of the model at ``path`` (a ``.sluggie`` or its folder; module docstring)."""
    path = resolve_path(path)
    folder = os.path.dirname(path) if os.path.isfile(path) else path
    if not os.path.isdir(folder):
        return ModelIcons(None, None, None, f'{path} not found')
    home, reason = high_poly_folder(folder)
    if home is None:
        return ModelIcons(None, None, None, reason)
    found = {view: os.path.join(home, ICON_SUBDIR, name) for view, name in FILES.items()}
    found = {view: p if os.path.isfile(p) else None for view, p in found.items()}
    missing = [f'{ICON_SUBDIR}/{FILES[view]}' for view in REQUIRED if found[view] is None]
    problem = None
    if missing:
        problem = (f'{" and ".join(missing)} missing in {os.path.basename(home)}: no portraits imported '
                   '(export the icons first, menu [1])')
    return ModelIcons(home, found['side'], found['front'], problem, found[ALT_VIEW])
