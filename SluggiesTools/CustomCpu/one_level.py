"""One-level versions of the per-level helpers that ``item_defense``, ``dive`` and ``items`` were written
against. Those modules come from a build (the Slugout 0.8 patcher) that puts three custom levels into one DOL
and picks values and addresses by a flag at run time; the custom CPU level is one level, so every pick is its
first value and every "per level" triple holds one value three times.

Its flag byte holds 4 / 1 / 2 for its levels 4 / 5 / 6; ours holds 1 during a custom match. The few places
that compare the flag with a number therefore take that tool's "level 5" branch, whose values here are the
custom level's own.
"""

try:
    from .behaviour_base import FLAG
    from .behaviour_base import gate as _gate
except ImportError:
    from CustomCpu.behaviour_base import FLAG
    from CustomCpu.behaviour_base import gate as _gate

__all__ = ['FLAG', 'gate', 'pair', 'pick', 'pick_addr']


def gate(a, reg, skip, feature=None):
    """Branch to ``skip`` unless a custom match is on. Clobbers ``reg`` and cr0."""
    return _gate(a, reg, skip)


def pair(module, name):
    """The (level 4, 5, 6) values of ``module``'s constant ``name``: one value here."""
    value = getattr(module, name)
    return value, value, value


def pick(a, reg, *values):
    """Load the level's immediate: the first one."""
    return a.li(reg, values[0])


def pick_addr(a, reg, *values):
    return a.load_addr(reg, values[0])
