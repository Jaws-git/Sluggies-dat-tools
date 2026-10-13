"""Sound volumes: the user's per-sound volume bytes for ``MY2.brsar``.

Every sound entry of the archive's INFO section (wave, sequence and stream sounds alike) holds a ``u8`` volume
at ``+0x14``, 0-127 (``brsar.Sound.volume``). Vanilla US archive (2026-10-12): 1,050 sounds, 0-127, mean 103.1;
107 is the default (666 sounds), the stream sounds (music) sit lower (36-117, mean 80.8). The byte only scales
down from the wave's own level: 127 is the loudest it plays.

**The library.** ``sound_volumes.json`` beside ``1_Input`` (user data, like ``Sound_FX/``)::

    {"format": 1, "volumes": {"bb_swing_m": 90, "STRM_TITLE": 110}}

keyed by the sound's name (INFO IDs never move; names are what a reader sees). A sound set back to its stock
volume leaves the library. ``apply`` writes every library volume into an archive's bytes in place (nothing
moves): the roster runner calls it on every run, after the sound effects, so "Patch Game" keeps them;
``set_volume`` stores one and writes ``3_Output_Dat/sound_NA/MY2.brsar`` right away (the GUI's "Set volume"
buttons; no staging). Sounds the roster steps add (a character's own voice copying a stock clip) keep the stock
source's volume.
"""

import json
import os

from Sound import brsar

LIBRARY_FILE = 'sound_volumes.json'      # beside 1_Input
FORMAT = 1
MAX_VOLUME = 127
VOLUME_OFFSET = 0x14                     # in a sound entry


class VolumeError(ValueError):
    pass


def library_path(root_dir):
    return os.path.join(root_dir, LIBRARY_FILE)


def load_library(root_dir):
    """``{sound name: volume}`` of ``sound_volumes.json`` (empty without one)."""
    path = library_path(root_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            doc = json.load(handle)
    except OSError as exc:
        raise VolumeError(f'{path}: {exc.strerror or exc}') from exc
    except ValueError as exc:
        raise VolumeError(f'{path}: not valid JSON: {exc}') from exc
    if not isinstance(doc, dict) or doc.get('format') != FORMAT or not isinstance(doc.get('volumes', {}), dict):
        raise VolumeError(f'{path}: not a sound volume library of format {FORMAT}')
    out = {}
    for name, volume in (doc.get('volumes') or {}).items():
        if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= MAX_VOLUME:
            raise VolumeError(f'{path}: {name}: the volume must be a whole number 0-{MAX_VOLUME}')
        out[name] = volume
    return out


def save_library(root_dir, volumes):
    path = library_path(root_dir)
    doc = {'format': FORMAT, 'volumes': {name: volumes[name] for name in sorted(volumes)}}
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1)
    os.replace(path + '.tmp', path)


def check_volume(value):
    """``value`` (an int or the text of one) -> the volume; ``VolumeError`` outside 0-127."""
    try:
        volume = int(str(value).strip())
    except ValueError:
        raise VolumeError(f'{value!r} is not a whole number') from None
    if not 0 <= volume <= MAX_VOLUME:
        raise VolumeError(f'{volume} is outside 0-{MAX_VOLUME}')
    return volume


def write_volumes(data, volumes):
    """``(archive bytes, {name: (old, new)} of the sounds changed, names not in the archive)``: ``data`` with
    each named sound's volume byte set (``data`` itself when nothing changes)."""
    archive = brsar.Brsar(data)
    by_name = {s.name: s for s in archive.sounds()}
    offsets = archive.table(brsar.TABLE_SOUNDS)
    out, changed, missing = None, {}, []
    for name, volume in sorted(volumes.items()):
        sound = by_name.get(name)
        if sound is None:
            missing.append(name)
            continue
        if sound.volume == volume:
            continue
        if out is None:
            out = bytearray(archive.b)
        out[offsets[sound.index] + VOLUME_OFFSET] = volume
        changed[name] = (sound.volume, volume)
    return (archive.b if out is None else bytes(out)), changed, missing


def apply(data, root_dir):
    """``(archive bytes, log lines)``: ``data`` with every library volume in (``data`` as it is without any)."""
    try:
        volumes = load_library(root_dir)
    except VolumeError as exc:
        return data, [f'sound volumes not applied: {exc}']
    if not volumes:
        return data, []
    out, changed, missing = write_volumes(data, volumes)
    lines = [f'sound volume skipped: {name}: no sound of that name in the archive' for name in missing]
    if changed:
        lines.append(f'sound volumes: {len(changed)} set from {LIBRARY_FILE} '
                     f'({", ".join(f"{n} {old}->{new}" for n, (old, new) in changed.items())})')
    return (out if changed else data), lines


def current_volume(root_dir, name):
    """The volume sound ``name`` plays with in the game now: ``3_Output_Dat``'s archive when there is one, else
    ``1_Input``'s; None when no archive names it."""
    input_path, output_path = brsar.archive_paths(root_dir)
    path = output_path if os.path.isfile(output_path) else input_path
    for s in brsar.Brsar.from_file(path).sounds():
        if s.name == name:
            return s.volume
    return None


def game_volumes(root_dir):
    """``{sound name: (volume in the game now, stock volume)}``: the game's from ``3_Output_Dat``'s archive when
    there is one (else ``1_Input``'s, so equal to the stock one), the stock from ``1_Input``'s. Empty without an
    input archive."""
    input_path, output_path = brsar.archive_paths(root_dir)
    if not os.path.isfile(input_path):
        return {}
    stock = {s.name: s.volume for s in brsar.Brsar.from_file(input_path).sounds()}
    if not os.path.isfile(output_path):
        return {name: (v, v) for name, v in stock.items()}
    return {s.name: (s.volume, stock.get(s.name, s.volume)) for s in brsar.Brsar.from_file(output_path).sounds()}


def set_volume(root_dir, name, volume):
    """Store ``volume`` for sound ``name`` (dropped from the library when it is the stock volume) and write it
    into ``3_Output_Dat/sound_NA/MY2.brsar`` (made first, with the sound effects, when missing). Returns
    ``(old volume in the output, stock volume)``."""
    volume = check_volume(volume)
    input_path, output_path = brsar.archive_paths(root_dir)
    stock = next((s for s in brsar.Brsar.from_file(input_path).sounds() if s.name == name), None)
    if stock is None:
        raise VolumeError(f'{name}: no sound of that name in 1_Input/{brsar.ARCHIVE.replace(os.sep, "/")}')
    if not os.path.isfile(output_path):
        from Sound import effects
        effects.sync_output(root_dir)                      # the output archive from 1_Input's, effects in
    with open(output_path, 'rb') as handle:
        base = handle.read()
    old = next((s.volume for s in brsar.Brsar(base).sounds() if s.name == name), None)
    if old is None:
        raise VolumeError(f'{name}: no sound of that name in 3_Output_Dat/{brsar.ARCHIVE.replace(os.sep, "/")}')
    library = load_library(root_dir)
    if volume == stock.volume:
        library.pop(name, None)
    else:
        library[name] = volume
    save_library(root_dir, library)
    out, changed, _missing = write_volumes(base, {name: volume})
    if changed:
        with open(output_path + '.tmp', 'wb') as handle:
            handle.write(out)
        os.replace(output_path + '.tmp', output_path)
    return old, stock.volume
