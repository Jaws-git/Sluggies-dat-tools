"""The "Set volume" popup and the "Replace gain" field the Sound FX, Voices and Music tabs share.

Asks for a sound's volume byte (0-127, ``Sound/volumes``), pre-filled with the volume the sound has in the game
now (``3_Output_Dat/sound_NA/MY2.brsar``, else ``1_Input``'s), and writes it right away (``volumes.set_volume``:
the output archive and ``sound_volumes.json``, which "Patch Game" applies again). No staging.

The **Volume** column shows each sound's volume byte in the game (``volumes.game_volumes``: the output archive,
else ``1_Input``'s), blue with the stock value beside it when changed.

The toolbar's **Replace gain** (dB, default 0, +-24) scales the sound file the tab's Replace... encodes
(``clips.apply_gain``); a finished ``.rwav`` (Voices) is taken as it is.
"""

import os

_DIM = (200, 200, 200, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_CHANGED = (110, 170, 255, 255)

TOOLTIP = ('Set the sound\'s volume byte in the sound archive (0-127; 127 is the loudest, it cannot go above the '
           'wave\'s own level). Written to 3_Output_Dat/sound_NA/MY2.brsar right away and kept in '
           'sound_volumes.json, so "Patch Game" keeps it.')


def ask_volume(app, name, status, tag, on_done=None):
    """Open the popup for sound ``name``. ``status(text, color)`` reports to the tab, ``tag`` prefixes log lines
    (``'sound fx'``), ``on_done(volume, stock volume)`` runs after a write."""
    from Sound import volumes
    if app.busy:
        status('A command is running: try again when it is done.', _WARN)
        return
    root = app.root_dir
    try:
        current = volumes.current_volume(root, name)
    except Exception as exc:
        status(f'Could not read the volume of {name}: {exc}', _ERROR)
        return
    if current is None:
        status(f'{name} is not in the sound archive.', _ERROR)
        return

    def answer(text):
        if app.busy:
            status('A command is running: try again when it is done.', _WARN)
            return
        try:
            volume = volumes.check_volume(text)
            old, stock = volumes.set_volume(root, name, volume)
        except (volumes.VolumeError, OSError) as exc:
            status(f'Volume not set: {exc}', _ERROR)
            app.log_line(f'[{tag}] {name}: volume not set: {exc}', _WARN)
            return
        except Exception as exc:
            status(f'Volume not set: {type(exc).__name__}: {exc}', _ERROR)
            app.log_line(f'[{tag}] {name}: volume not set: {type(exc).__name__}: {exc}', _WARN)
            return
        where = '3_Output_Dat/sound_NA/MY2.brsar written' if old != volume else 'unchanged'
        done = f'{name}: volume {old} -> {volume} (stock {stock}): {where}.'
        status(done, _OK)
        app.log_line(f'[{tag}] {done}', _OK)
        if on_done is not None:
            on_done(volume, stock)
    app.ask_text(f'Set volume: {name}',
                 f'Volume of {name}, 0-{volumes.MAX_VOLUME} (now {current}',
                 answer, lambda: None, initial=str(current))


# ------------------------------------------------------------------ the Volume column
def read_volumes(root_dir):
    """``volumes.game_volumes`` for a tab's worker thread: ``{}`` when it cannot be read."""
    try:
        from Sound import volumes
        return volumes.game_volumes(root_dir)
    except Exception:
        return {}


def add_volume_cell(table_volumes, name):
    """The Volume cell of sound ``name`` (``table_volumes``: ``read_volumes``' dict): blue with the stock value
    when changed, empty when unknown."""
    import dearpygui.dearpygui as dpg
    current, stock = table_volumes.get(name, (None, None))
    if current is None:
        dpg.add_text('')
    elif current != stock:
        dpg.add_text(f'{current} (stock {stock})', color=_CHANGED)
    else:
        dpg.add_text(str(current), color=_DIM)


# ------------------------------------------------------------------ the Replace gain
GAIN_TOOLTIP = ('Gain applied to the sound file a Replace... encodes, in decibels (0 = as it is; +6 dB about doubles '
                'the level, -6 dB halves it). Peaks pushed above full scale are clipped (the log says so). Not '
                'applied to a finished .rwav.')


def add_gain_field(tag):
    """The toolbar's "Replace gain" field (dB, default 0)."""
    import dearpygui.dearpygui as dpg
    from Sound import clips
    dpg.add_text('Replace gain')
    field = dpg.add_input_float(tag=tag, default_value=0.0, width=110, step=1.0, step_fast=6.0, format='%+.1f dB',
                                min_value=-clips.MAX_GAIN_DB, max_value=clips.MAX_GAIN_DB, min_clamped=True,
                                max_clamped=True)
    with dpg.tooltip(field):
        dpg.add_text(GAIN_TOOLTIP, wrap=360)


def gain_of(tag):
    """The gain field's value in dB (0 when it is missing)."""
    import dearpygui.dearpygui as dpg
    from Sound import clips
    value = float(dpg.get_value(tag) or 0.0) if dpg.does_item_exist(tag) else 0.0
    return max(-clips.MAX_GAIN_DB, min(clips.MAX_GAIN_DB, round(value, 1)))


def gain_label(gain_db):
    """`` (+3.0 dB)`` for a non-zero gain, else ''."""
    return f' ({gain_db:+.1f} dB)' if gain_db else ''
