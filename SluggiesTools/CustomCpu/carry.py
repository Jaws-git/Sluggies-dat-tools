"""The custom CPU level across rebuilds of ``main.dol``: read its record before, build everything from it after.

A roster run resets ``main.dol`` to ``1_Input`` (the hooks and the block are gone; the two ``dt_na.dat``
records survive, ``menu_files``), and the game options rebuild the DOL sections when an option is turned off
on a DOL without a roster. Both call ``detect`` first and ``apply`` afterwards, as for the game options. The
record in the DOL is the only state.
"""

try:
    from ..Dol import dolfile
    from . import engine, menu_files, settings
except ImportError:
    from Dol import dolfile
    from CustomCpu import engine, menu_files, settings


def detect(image: dolfile.DolImage) -> engine.Record | None:
    """The custom level's record in ``image``, None when it is off. Raises ``engine.CustomCpuError`` when the
    record cannot be read."""
    return engine.detect(image)


def apply(image: dolfile.DolImage, dat, record: engine.Record, vanilla: dolfile.DolImage | None, reserved=(),
          input_dat=None) -> list[str]:
    """The DOL part and the two ``dt_na.dat`` files (``dat``: the output's ``DatFile``; None: the DOL only)."""
    log = engine.apply_dol(image, record, settings.image_get(vanilla) if vanilla is not None else None)
    if vanilla is not None:
        log += clear_global_edits(image, vanilla)
    if dat is None:
        log.append('custom CPU level: dt_na.dat is missing, the popup files were not written')
    elif vanilla is None:
        raise engine.CustomCpuError('custom CPU level: the popup files need the original main.dol in 1_Input')
    else:
        log += menu_files.apply_dat(image, dat, vanilla, record.button, reserved, input_dat)
    return log


def clear_global_edits(image: dolfile.DolImage, vanilla: dolfile.DolImage) -> list[str]:
    """Put the CPU tables that used to be Gameplay tab settings back to ``1_Input`` in ``image``: they are
    custom level settings now, and levels 1-4 play the clean game's tables. An older version's edits there
    would otherwise stay in the DOL for levels 1-4 until the next roster run."""
    count = 0
    for at, size in settings.FORMER_GAMEPLAY_RANGES:
        if image.is_mapped(at, size) and vanilla.is_mapped(at, size):
            stock = vanilla.read(at, size)
            count += sum(a != b for a, b in zip(image.read(at, size), stock))
            image.write(at, stock)
    if not count:
        return []
    return [f'custom CPU level: {count} value{"s" if count != 1 else ""} of the star usage, charge odds, star swing '
            'modifier and preferred pitch tables that an older Gameplay tab wrote are back to 1_Input (they are '
            'custom level settings now)']


def remove(image: dolfile.DolImage, dat, vanilla: dolfile.DolImage | None) -> list[str]:
    """Turn the custom level off: stock words, the block's magic cleared, the two files back to stock."""
    was_on = engine.unhook(image)
    log = ['custom CPU level: off' if was_on else 'custom CPU level: already off']
    if vanilla is not None:
        log += menu_files.remove_dat(image, dat, vanilla)
    return log
