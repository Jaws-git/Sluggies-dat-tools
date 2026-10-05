"""Registry of the roster steps, in run order.

Each step's module registers it with ``@register('<key>')``; the runner
(``runner.py``, menu [7]) runs them in ``STEPS`` order. A step function
takes a ``RosterContext`` and returns log lines; it changes ``ctx.dol`` and
``ctx.dat`` only; the runner writes the files once all steps have run.
"""

import importlib
from dataclasses import dataclass, field
from typing import Callable

# (key = the module that registers it, title), in run order
STEPS = (
    ('dol_hammerspace', 'DOL hammerspace'),
    ('layout_file', 'Select layout file in DAT hammerspace'),
    ('ids', 'New character IDs'),
    ('model_dirs', 'Own model directories'),
    ('wheels', 'Colour wheels'),
    ('icons', 'Icons'),
    ('grid', 'Exhibition draft grid'),
    ('names', 'Names'),
)


@dataclass
class RosterContext:
    dol: object                    # Dol.dolfile.DolImage
    dat: object | None             # datfile.DatFile, or None when a step needs no DAT
    config: dict
    state: dict = field(default_factory=dict)   # shared between steps of one run
    icon_dir: str | None = None    # where the config's portrait PNGs are (None: 1_Input/_Icons; a derived state's folder)
    input_dol: object | None = None  # 1_Input/main.dol (DolImage): the stock directories model_dirs copies from
    input_dat: object | None = None  # 1_Input/dt_na.dat (anything with read(offset, size))


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
