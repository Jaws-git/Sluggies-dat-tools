"""Registry of the roster steps, in run order.

Each step's module registers it with ``@register('<key>')``; the runner
(``runner.py``, menu [10]) runs them in ``STEPS`` order. A step function
takes a ``RosterContext`` and returns log lines; it changes ``ctx.dol`` and
``ctx.dat`` only, so the runner can record (and later undo) everything.
"""

import importlib
from dataclasses import dataclass, field
from typing import Callable

# (key = the module that registers it, title), in run order
STEPS = (
    ('dol_hammerspace', 'DOL hammerspace'),
    ('layout_file', 'Select layout file in DAT hammerspace'),
    ('ids', 'New character IDs'),
    ('wheels', 'Colour wheels'),
    ('icons', 'Icons'),
    ('grid', 'Exhibition draft grid'),
    ('names', 'Names'),
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
    title: str
    apply: Callable[[RosterContext], list]


_REGISTRY: dict[str, Callable] = {}


def register(key: str):
    if key not in {k for k, _t in STEPS}:
        raise KeyError(f'unknown roster step {key!r}')

    def decorate(fn):
        _REGISTRY[key] = fn
        return fn
    return decorate


def all_steps() -> list[Step]:
    for key, _title in STEPS:
        importlib.import_module(f'{__package__}.{key}' if __package__ else key)
    return [Step(key, title, _REGISTRY[key]) for key, title in STEPS]
