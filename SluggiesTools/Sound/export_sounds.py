"""Sound export: the game's sounds as 16-bit WAV files (``start.py --export-sounds``).

Reads ``1_Input/sound_NA/MY2.brsar`` (and the ``stream/*.brstm`` music next to it, both copied from the
game's ``files/sound_NA`` folder) and writes ``2_Output_Models/_SOUNDS/``, laid out as the archive is::

    _SOUNDS/<group>/<sound>.wav          wave sounds (voices, effects), one folder per archive group
                                         (group_v001 ... voice families, group_sel_v the select-screen calls,
                                         group_bb the baseball effects, group_st* stadiums, ...)
    _SOUNDS/<group>/_unreferenced/...    waves of a group's files no sound entry plays
    _SOUNDS/stream/<sound>.wav           stream sounds (music, jingles): the external .brstm's folder
    _SOUNDS/index.json                   every sound: INFO ID, name, type, group, file, player, volume,
                                         rate, channels, length, loop, and the WAV written for it

Sequence sounds (SEQ: note data played through a sound bank, like MIDI) have no single waveform and are
listed in the index only. All sounds are DSP-ADPCM, which decodes to exactly 16-bit PCM, so a 16-bit WAV
holds them without a second lossy step; looped sounds keep their loop points in a ``smpl`` chunk.
"""

import argparse
import json
import os
import shutil
import sys
import time

SOUND_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(SOUND_DIR, '..'))
ROOT_DIR = os.path.normpath(os.path.join(TOOLS_DIR, '..'))
for _path in (TOOLS_DIR, ROOT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger as _slogger                                   # noqa: E402
_slogger.configure()

from Sound import brsar, brstm, wavfile                      # noqa: E402

SOURCE = 'sound.export_sounds'
ARCHIVE_NAME = 'MY2.brsar'
INPUT_SOUND_DIR = os.path.join(ROOT_DIR, '1_Input', 'sound_NA')
OUTPUT_DIR = os.path.join(ROOT_DIR, '2_Output_Models', '_SOUNDS')
INDEX_NAME = 'index.json'
UNREFERENCED_DIR = '_unreferenced'


class ExportSoundsError(Exception):
    pass


def _safe_name(name):
    """A file-system safe version of an archive name."""
    cleaned = ''.join(c if c.isalnum() or c in '-_. ' else '_' for c in name).strip()
    return cleaned or 'unnamed'


def _seconds(samples, rate):
    return round(samples / rate, 3) if rate else None


def _loop_record(loop):
    return {'start': int(loop[0]), 'end': int(loop[1])} if loop else None


def plan_waves(archive):
    """Every archived wave: ``(record, Wave, relative output path)`` for the wave sounds, then the waves no
    sound plays. ``record`` is the index entry (without the decoded data)."""
    groups = archive.groups()
    files = archive.files()
    players = {p.index: p.name for p in archive.players()}
    group_of_file = {}
    for group in groups:
        for item in group.items:
            group_of_file.setdefault(item.file_id, group)
    file_cache = {}

    def file_parts(file_id):
        if file_id not in file_cache:
            parts = archive.file_bytes(file_id, groups, files)
            if parts is None:
                raise ExportSoundsError(f'file {file_id} is not stored in the archive')
            file_cache[file_id] = parts
        return file_cache[file_id]

    def folder(file_id):
        group = group_of_file.get(file_id)
        return _safe_name(group.name) if group and group.name else f'file_{file_id}'

    planned, referenced = [], {}
    for sound in archive.sounds():
        if sound.type != brsar.WAVE:
            continue
        rwsd, rwar = file_parts(sound.file_id)
        wave_index = brsar.rwsd_wave_index(rwsd, sound.wave_entry)
        referenced.setdefault(sound.file_id, set()).add(wave_index)
        wave = brsar.parse_rwav(brsar.rwar_wave_bytes(rwar, wave_index))
        rel = os.path.join(folder(sound.file_id), _safe_name(sound.name) + '.wav')
        record = {'id': sound.index, 'name': sound.name, 'type': sound.type_name, 'file': sound.file_id,
                  'wave': wave_index, 'group': folder(sound.file_id), 'player': players.get(sound.player_id),
                  'volume': sound.volume, 'priority': sound.priority, 'rate': wave.rate,
                  'channels': len(wave.channels), 'samples': wave.samples,
                  'seconds': _seconds(wave.samples, wave.rate), 'loop': _loop_record(wave.loop),
                  'path': rel.replace(os.sep, '/')}
        planned.append((record, wave, rel))
    extras = []
    for file_id in sorted(file_cache):
        rwsd, rwar = file_cache[file_id]
        for wave_index in range(brsar.rwar_count(rwar)):
            if wave_index in referenced.get(file_id, ()):
                continue
            wave = brsar.parse_rwav(brsar.rwar_wave_bytes(rwar, wave_index))
            rel = os.path.join(folder(file_id), UNREFERENCED_DIR, f'file{file_id}_wave{wave_index}.wav')
            record = {'id': None, 'name': f'file{file_id}_wave{wave_index}', 'type': 'wave', 'file': file_id,
                      'wave': wave_index, 'group': folder(file_id), 'player': None, 'volume': None,
                      'priority': None, 'rate': wave.rate, 'channels': len(wave.channels),
                      'samples': wave.samples, 'seconds': _seconds(wave.samples, wave.rate),
                      'loop': _loop_record(wave.loop), 'path': rel.replace(os.sep, '/')}
            extras.append((record, wave, rel))
    return planned, extras


def export_waves(archive, out_dir):
    """Decode and write every archived wave; returns ``(index records, bytes written)``."""
    planned, extras = plan_waves(archive)
    everything = planned + extras
    _slogger.info(f'{len(planned)} wave sounds in {len({r["group"] for r, _, _ in planned})} groups'
                  + (f', {len(extras)} unreferenced waves' if extras else ''), source=SOURCE)
    started = time.time()
    decoded = brsar.decode_waves([wave for _, wave, _ in everything])
    _slogger.info(f'Decoded {len(everything)} waves in {time.time() - started:.1f} s', source=SOURCE)
    written = 0
    for (record, wave, rel), channels in zip(everything, decoded):
        path = os.path.join(out_dir, rel)
        wavfile.write_wav(path, wave.rate, channels, wave.loop)
        written += os.path.getsize(path)
    return [record for record, _, _ in everything], written


def export_streams(archive, sound_dir, out_dir):
    """Decode every stream sound's ``.brstm`` from ``sound_dir``; returns ``(index records, bytes written,
    missing stream files)``."""
    files = archive.files()
    players = {p.index: p.name for p in archive.players()}
    records, written, missing = [], 0, []
    streams = [s for s in archive.sounds() if s.type == brsar.STRM]
    _slogger.info(f'{len(streams)} stream sounds', source=SOURCE)
    for sound in streams:
        entry = files[sound.file_id]
        external = entry.external_path.replace('\\', '/')
        record = {'id': sound.index, 'name': sound.name, 'type': sound.type_name, 'file': sound.file_id,
                  'source': external or None, 'player': players.get(sound.player_id), 'volume': sound.volume,
                  'priority': sound.priority, 'rate': None, 'channels': None, 'samples': None, 'seconds': None,
                  'loop': None, 'path': None}
        records.append(record)
        if not external:
            _slogger.warning(f'{sound.name}: stream sound without an external file', source=SOURCE)
            continue
        source = os.path.join(sound_dir, *external.split('/'))
        if not os.path.isfile(source):
            missing.append(external)
            continue
        with open(source, 'rb') as handle:
            stream = brstm.parse(handle.read())
        started = time.time()
        channels = brstm.decode(stream)
        rel = os.path.join(os.path.dirname(external) or 'stream', _safe_name(sound.name) + '.wav')
        path = os.path.join(out_dir, *rel.split('/'))
        wavfile.write_wav(path, stream.rate, channels, stream.loop)
        written += os.path.getsize(path)
        record.update(rate=stream.rate, channels=stream.channels, samples=stream.samples,
                      seconds=_seconds(stream.samples, stream.rate), loop=_loop_record(stream.loop),
                      path=rel.replace(os.sep, '/'))
        _slogger.info(f'{external} -> {rel.replace(os.sep, "/")} ({stream.channels} ch, {stream.rate} Hz, '
                      f'{stream.samples / stream.rate:.1f} s, {time.time() - started:.1f} s)', source=SOURCE)
    return records, written, missing


def sequence_records(archive):
    banks = {b.index: b.name for b in archive.banks()}
    players = {p.index: p.name for p in archive.players()}
    return [{'id': s.index, 'name': s.name, 'type': s.type_name, 'file': s.file_id, 'bank': banks.get(s.bank),
             'player': players.get(s.player_id), 'volume': s.volume, 'priority': s.priority, 'path': None}
            for s in archive.sounds() if s.type == brsar.SEQ]


def clear_output(out_dir):
    """Remove an earlier export (recognised by its index file); another folder is left as it is."""
    if os.path.isfile(os.path.join(out_dir, INDEX_NAME)):
        shutil.rmtree(out_dir)


def export(sound_dir=INPUT_SOUND_DIR, out_dir=OUTPUT_DIR):
    archive_path = os.path.join(sound_dir, ARCHIVE_NAME)
    if not os.path.isfile(archive_path):
        raise ExportSoundsError(
            f'{archive_path} not found. Copy the game\'s files/sound_NA folder ({ARCHIVE_NAME} and stream/) '
            f'into 1_Input/sound_NA.')
    started = time.time()
    archive = brsar.Brsar.from_file(archive_path)
    sounds = archive.sounds()
    _slogger.info(f'{archive_path}: {len(sounds)} sounds, {len(archive.files())} files, '
                  f'{len(archive.groups())} groups', source=SOURCE)
    clear_output(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    wave_records, wave_bytes = export_waves(archive, out_dir)
    stream_records, stream_bytes, missing = export_streams(archive, sound_dir, out_dir)
    sequences = sequence_records(archive)
    if sequences:
        _slogger.info(f'{len(sequences)} sequence sounds (note data for a sound bank) listed in the index only',
                      source=SOURCE)
    if missing:
        _slogger.warning(f'{len(missing)} stream file(s) missing in {sound_dir}: {", ".join(missing[:5])}'
                         + (' ...' if len(missing) > 5 else '') + '. Copy the game\'s sound_NA/stream folder too.',
                         source=SOURCE)
    records = sorted(wave_records + stream_records + sequences,
                     key=lambda r: (r['id'] is None, r['id'] if r['id'] is not None else 0, r['name']))
    index = {'archive': ARCHIVE_NAME, 'sounds': records,
             'groups': [{'index': g.index, 'name': g.name, 'files': [i.file_id for i in g.items],
                         'wave_bytes': g.wave_size} for g in archive.groups()],
             'players': [{'index': p.index, 'name': p.name, 'heap': p.heap_size} for p in archive.players()]}
    with open(os.path.join(out_dir, INDEX_NAME), 'w', encoding='utf-8') as handle:
        json.dump(index, handle, indent=1)
    written = sum(1 for r in records if r.get('path'))
    _slogger.info(f'Sound export complete: {written} WAV files, {(wave_bytes + stream_bytes) / 2**20:.0f} MB, '
                  f'in {out_dir} ({time.time() - started:.0f} s)', source=SOURCE)
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description='Export the game\'s sounds as WAV files.')
    parser.add_argument('--sound-dir', default=INPUT_SOUND_DIR,
                        help='the folder holding MY2.brsar and stream/ (default: 1_Input/sound_NA)')
    parser.add_argument('--out', default=OUTPUT_DIR, help='output folder (default: 2_Output_Models/_SOUNDS)')
    args = parser.parse_args(argv)
    try:
        export(args.sound_dir, args.out)
    except (ExportSoundsError, brsar.BrsarError, brstm.BrstmError) as exc:
        _slogger.error(str(exc), source=SOURCE)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
