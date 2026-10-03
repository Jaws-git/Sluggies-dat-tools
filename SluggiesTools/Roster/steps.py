"""Registry of roster-expansion steps, in plan order.

Each phase's module registers its step with ``@register('<key>')``; the
development injector (``runner.py``, menu [10]) runs every registered step
in ``PLANNED`` order and reports the rest as not built yet. A step function
takes a ``RosterContext`` and returns log lines; it changes ``ctx.dol`` and
``ctx.dat`` only, so the runner can record (and later undo) everything.
"""

import importlib
from dataclasses import dataclass, field
from typing import Callable

# (key, plan phase, title)
PLANNED = (
    ('dol_hammerspace', '1', 'DOL hammerspace'),
    ('layout_file', '2', 'Select layout file in DAT hammerspace'),
    ('ids', '3', 'Uncapped character IDs'),
    ('wheels', '4', 'Colour wheels'),
    ('icons', '5', 'Icons for new IDs and variants'),
    ('grid', '6', 'Exhibition grid columns'),
    ('names', '8', 'User-set names'),
)
# Modules that register steps (imported on demand, so a missing one only hides its step).
STEP_MODULES = (
    'dol_hammerspace',
    'layout_file',
    'ids',
    'wheels',
    'icons',
    'grid',
)


@dataclass
class RosterContext:
    dol: object                    # Dol.dolfile.DolImage
    dat: object | None             # ledger.DatFile, or None when a step needs no DAT
    config: dict
    state: dict = field(default_factory=dict)   # shared between steps of one run


@dataclass(frozen=True)
class Step:
    key: str
    phase: str
    title: str
    apply: Callable[[RosterContext], list]


_REGISTRY: dict[str, Callable] = {}


def register(key: str):
    if key not in {k for k, _p, _t in PLANNED}:
        raise KeyError(f'unknown roster step {key!r}')

    def decorate(fn):
        _REGISTRY[key] = fn
        return fn
    return decorate


def _load_modules() -> None:
    for name in STEP_MODULES:
        try:
            importlib.import_module(f'{__package__}.{name}' if __package__ else name)
        except ModuleNotFoundError as exc:
            if exc.name not in (name, f'{__package__}.{name}'):
                raise


def implemented() -> list[Step]:
    _load_modules()
    return [Step(k, p, t, _REGISTRY[k]) for k, p, t in PLANNED if k in _REGISTRY]


def not_built() -> list[tuple[str, str, str]]:
    _load_modules()
    return [(k, p, t) for k, p, t in PLANNED if k not in _REGISTRY]
