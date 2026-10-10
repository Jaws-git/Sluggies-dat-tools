"""Synthetic sound files for the tests: a mini BRSAR archive (RWSD + RWAR + RWAV inside) and a mini BRSTM stream,
laid out as the readers in ``Sound/`` expect them (``Sound/brsar.py`` and ``Sound/brstm.py`` document the layouts).
No production data is read (the ``.clinerules`` rule); the sample data is random DSP-ADPCM, which any bytes are.
"""

import random
import struct

from Sound import dsp_adpcm
from Sound.dsp_adpcm import Lane

FRAME = dsp_adpcm.FRAME_BYTES


def p32(*values):
    return struct.pack(f'>{len(values)}I', *values)


def ref(value, kind=0):
    """An INFO / HEAD ref: {u8 is_offset = 1, u8 type, u16 pad, u32 value}."""
    return struct.pack('>BBHI', 1, kind, 0, value)


def align(data, size=0x20):
    pad = (-len(data)) % size
    return data + bytes(pad)


def random_coefs(rng, strong=False):
    """8 coefficient pairs; ``strong`` ones overshoot so the decoder clips."""
    limit = 32767 if strong else 2048
    return [rng.randint(-limit, limit) for _ in range(16)]


def random_adpcm(rng, samples):
    """Random frames for ``samples`` samples (header scale kept moderate so values stay audible-ish)."""
    frames = (samples + 13) // 14
    out = bytearray()
    for _ in range(frames):
        out.append((rng.randint(0, 7) << 4) | rng.randint(0, 12))
        out += bytes(rng.randint(0, 255) for _ in range(7))
    return bytes(out)


# ------------------------------------------------------------------------------------------------- RWAV / RWAR / RWSD

def build_rwav(channels, rate, looped=False, loop_start_nibble=2, fmt=2):
    """``channels``: ADPCM: ``[(adpcm bytes, samples, coefs, h1, h2)]``; PCM16 (``fmt=1``): ``[list of ints]``.
    ADPCM lengths are given in nibbles (loop end = the length) as the game stores them."""
    nch = len(channels)
    if fmt == 2:
        samples = channels[0][1]
        frames = (samples + 13) // 14
        length_nibbles = frames * 16 - (frames * 14 - samples)        # header nibbles included
    else:
        samples = len(channels[0])
        length_nibbles = samples
    info = bytearray()
    info += struct.pack('>BBBB', fmt, 1 if looped else 0, nch, rate >> 16)
    info += struct.pack('>HH', rate & 0xFFFF, 0)
    info += p32(loop_start_nibble if looped else 0, length_nibbles)
    chan_table = 0x1C
    info += p32(chan_table, 0)                                            # channel table offset, data location
    info += p32(0)                                                        # pad to 0x1C
    assert len(info) == chan_table
    info += p32(*([0] * nch))                                             # channel table, filled below
    data = bytearray()
    channel_infos = []
    for c, channel in enumerate(channels):
        if fmt == 2:
            adpcm_bytes, _, coefs, h1, h2 = channel
            payload = align(adpcm_bytes, 8)
        else:
            payload = struct.pack(f'>{len(channel)}h', *channel)
            coefs, h1, h2 = [0] * 16, 0, 0
        channel_infos.append((len(data), coefs, h1, h2))
        data += payload
    for c, (data_offset, coefs, h1, h2) in enumerate(channel_infos):
        struct.pack_into('>I', info, chan_table + 4 * c, len(info))
        adpcm_at = len(info) + 0x1C
        info += p32(data_offset, adpcm_at if fmt == 2 else 0, 0x01000000, 0x01000000, 0, 0, 0)    # 0x1C bytes
        assert len(info) == adpcm_at
        info += struct.pack('>16h', *coefs) + struct.pack('>HHhhHhh', 0, 0, h1, h2, 0, 0, 0)
    info_chunk = align(b'INFO' + p32(8 + len(info)) + bytes(info))
    data_chunk = b'DATA' + p32(8 + len(data)) + bytes(data)
    header = b'RWAV' + struct.pack('>HHIHH', 0xFEFF, 0x0102, 0, 0x20, 2)
    header += p32(0x20, len(info_chunk), 0x20 + len(info_chunk), len(data_chunk))
    header = align(header)
    out = bytearray(header + info_chunk + data_chunk)
    struct.pack_into('>I', out, 8, len(out))
    return bytes(out)


def build_rwar(rwavs):
    tabl = b'TABL' + p32(0, len(rwavs))
    entries = b''
    data = bytearray(b'DATA' + p32(0))
    data = bytearray(align(data))                                         # first RWAV at DATA + 0x20
    for rwav in rwavs:
        entries += ref(len(data)) + p32(len(rwav))
        data += align(rwav)
    tabl = align(tabl + entries)
    tabl = tabl[:4] + p32(len(tabl)) + tabl[8:]
    struct.pack_into('>I', data, 4, len(data))
    header = align(b'RWAR' + struct.pack('>HHIHH', 0xFEFF, 0x0100, 0, 0x20, 2) + p32(0x20, len(tabl), 0x20 + len(tabl), len(data)))
    out = bytearray(header + tabl + bytes(data))
    struct.pack_into('>I', out, 8, len(out))
    return bytes(out)


def build_rwsd(wave_indexes):
    """One DATA entry per element, its first note naming that wave."""
    count = len(wave_indexes)
    body = bytearray(p32(count) + b''.join(ref(0) for _ in wave_indexes))   # entry refs filled below
    for i, wave in enumerate(wave_indexes):
        entry_at = len(body)
        struct.pack_into('>I', body, 4 + 8 * i + 4, entry_at)
        notes_at = entry_at + 0x18
        body += ref(0) + ref(0) + ref(notes_at)
        note_at = notes_at + 12
        body += p32(1) + ref(note_at)
        body += p32(wave, 0, 0, 0)
    data = align(b'DATA' + p32(8 + len(body)) + bytes(body))
    header = align(b'RWSD' + struct.pack('>HHIHH', 0xFEFF, 0x0103, 0, 0x20, 1) + p32(0x20, len(data)))
    out = bytearray(header + data)
    struct.pack_into('>I', out, 8, len(out))
    return bytes(out)


# ------------------------------------------------------------------------------------------------- BRSAR

def build_brsar(names, sounds, banks, players, files, groups):
    """A minimal RSAR.

    names: list of strings (indexes used below). sounds: [{name, file, player, type, volume, priority, detail:
    bytes}]. banks: [{name, file}]. players: [{name, count, heap}]. files: [{size, wave_size, external: str or '',
    positions: [(group, item)]}]. groups: [{name (index or None), items: [{file, data: bytes, waves: bytes}]}]:
    the item bytes are laid into the FILE section, structure bytes and wave bytes apart.
    """
    # SYMB
    strings = bytearray()
    offsets = []
    for name in names:
        offsets.append(len(strings))
        strings += name.encode('latin1') + b'\0'
    table_at = 0x14                                                        # after the string table ref + 4 tree refs
    table = p32(len(names)) + b''.join(p32(table_at + 4 + 4 * len(names) + o) for o in offsets)
    symb_body = p32(table_at, 0, 0, 0, 0) + table + bytes(strings)
    symb = align(b'SYMB' + p32(8 + len(symb_body)) + symb_body)

    # FILE section: lay out every group's items
    file_body = bytearray(align(bytearray(b'FILE' + p32(0)), 0x20))
    group_layout = []
    for group in groups:
        items_layout = []
        g_off = len(file_body)
        for item in group['items']:
            items_layout.append({'file': item['file'], 'offset': len(file_body) - g_off, 'size': len(item['data'])})
            file_body += align(item['data'])
        g_size = len(file_body) - g_off
        w_off = len(file_body)
        for item, laid in zip(group['items'], items_layout):
            laid['wave_offset'] = len(file_body) - w_off
            laid['wave_size'] = len(item['waves'])
            file_body += align(item['waves'])
        group_layout.append((g_off, g_size, w_off, len(file_body) - w_off, items_layout))

    # INFO: five tables; everything relative to base = INFO + 8. Built in one buffer with fix-ups.
    info = bytearray(ref(0) * 5 + p32(0, 0))                              # table refs, filled below

    def add(blob):
        at = len(info)
        info.extend(blob)
        return at

    def table_of(entries):
        refs = bytearray(p32(len(entries)))
        for entry in entries:
            refs += ref(entry)
        return add(align(bytes(refs), 4))

    sound_entries = []
    for s in sounds:
        detail_at = add(align(s['detail'], 4))
        params_at = add(bytes(0x18))
        entry = bytearray(0x4C)
        struct.pack_into('>III', entry, 0, names.index(s['name']), s['file'], s['player'])
        entry[0xC:0x14] = ref(params_at)
        entry[0x14], entry[0x15], entry[0x16], entry[0x17] = s['volume'], s['priority'], s['type'], 0
        entry[0x18:0x20] = ref(detail_at, s['type'])
        sound_entries.append(add(bytes(entry)))
    bank_entries = [add(p32(names.index(b['name'])) + ref(0) + p32(b['file'], 0)) for b in banks]
    player_entries = [add(p32(names.index(p['name']), p['count'], p['heap'], 0)) for p in players]
    file_entries = []
    for f in files:
        ext_at = add(align(f['external'].encode('latin1') + b'\0', 4)) if f['external'] else 0
        pos_refs = []
        for g, i in f['positions']:
            pos_refs.append(add(p32(g, i)))
        pos_table = add(p32(len(pos_refs)) + b''.join(ref(p) for p in pos_refs))
        entry = p32(f['size'], f['wave_size'], 0xFFFFFFFF)
        entry += ref(ext_at) if f['external'] else bytes(8)
        entry += ref(pos_table)
        file_entries.append(add(entry))
    group_entries = []
    for group, (g_off, g_size, w_off, w_size, items_layout) in zip(groups, group_layout):
        item_ats = [add(p32(it['file'], it['offset'], it['size'], it['wave_offset'], it['wave_size'], 0))
                    for it in items_layout]
        items_table = add(p32(len(item_ats)) + b''.join(ref(a) for a in item_ats))
        name_index = names.index(group['name']) if group['name'] is not None else 0xFFFFFFFF
        entry = p32(name_index, 0xFFFFFFFF) + bytes(8) + p32(0, g_size, 0, w_size) + ref(items_table)
        group_entries.append((add(entry), g_off, w_off))
    tables = [table_of(sound_entries), table_of(bank_entries), table_of(player_entries), table_of(file_entries),
              table_of([g for g, _, _ in group_entries])]
    for which, at in enumerate(tables):
        info[8 * which:8 * which + 8] = ref(at)
    info_chunk = bytearray(align(b'INFO' + p32(8 + len(info)) + bytes(info)))
    symb_at = 0x40
    info_at = symb_at + len(symb)
    file_at = info_at + len(info_chunk)
    # group offsets are absolute: patch them now that the FILE section's position is known
    for (entry_at, g_off, w_off) in group_entries:
        struct.pack_into('>I', info_chunk, 8 + entry_at + 0x10, file_at + g_off)
        struct.pack_into('>I', info_chunk, 8 + entry_at + 0x18, file_at + w_off)
    struct.pack_into('>I', file_body, 4, len(file_body))
    header = bytearray(0x40)
    header[:4] = b'RSAR'
    struct.pack_into('>HHIHH', header, 4, 0xFEFF, 0x0104, 0, 0x40, 3)
    struct.pack_into('>6I', header, 0x10, symb_at, len(symb), info_at, len(info_chunk), file_at, len(file_body))
    out = bytearray(header + symb + bytes(info_chunk) + bytes(file_body))
    struct.pack_into('>I', out, 8, len(out))
    return bytes(out)


def wave_detail(entry):
    return p32(entry, 0x140, 0, 0, 0, 0x180, 0, 0)


def stream_detail():
    return p32(0, 0x20001, 0, 0, 0, 0xF, 0x1800000, 0)


def seq_detail(bank):
    return p32(0, bank, 0x140, 0, 0, 0x180, 0, 0)


# ------------------------------------------------------------------------------------------------- BRSTM

def build_brstm(channel_samples, rate, block_size=16, codec=2, looped=False, loop_start=0, rng=None):
    """A stream of ``len(channel_samples[0])`` samples per channel in blocks of ``block_size`` bytes per channel.
    ADPCM (codec 2): ``channel_samples`` are ignored for the bytes (random frames; ``rng``) and the expected
    output is returned too: ``(bytes, [int16 list per channel])``, every block's ADPC history being the real
    decoder history so a blockwise decode equals a continuous one. PCM16 (codec 1): the samples are written."""
    rng = rng or random.Random(5)
    nch = len(channel_samples)
    n = len(channel_samples[0])
    adpcm = codec == 2
    per_block = block_size // 8 * 14 if adpcm else block_size // 2
    blocks = (n + per_block - 1) // per_block
    last_samples = n - (blocks - 1) * per_block
    last_size = dsp_adpcm.samples_to_bytes(last_samples) if adpcm else last_samples * 2
    last_padded = (last_size + 31) // 32 * 32
    expected, data_per_channel, coefs_per_channel, hist = [], [], [], []
    for c in range(nch):
        if adpcm:
            coefs = random_coefs(rng)
            raw = random_adpcm(rng, n)
            lane = Lane(raw, n, coefs)
            decoded = dsp_adpcm.decode_reference(lane)
            expected.append(decoded)
            data_per_channel.append(raw)
            coefs_per_channel.append(coefs)
            block_hist = []
            for k in range(blocks):
                s = k * per_block
                block_hist.append((decoded[s - 1] if s >= 1 else 0, decoded[s - 2] if s >= 2 else 0))
            hist.append(block_hist)
        else:
            expected.append([int(v) for v in channel_samples[c]])
            data_per_channel.append(struct.pack(f'>{n}h', *channel_samples[c]))
            coefs_per_channel.append(None)
            hist.append([(0, 0)] * blocks)
    head_size = 0x100
    adpc_size = (8 + blocks * nch * 4 + 31) // 32 * 32 if adpcm else 0
    data_off = 0x40 + head_size + adpc_size
    head = bytearray(head_size)
    head[:4] = b'HEAD'
    struct.pack_into('>I', head, 4, head_size)
    base = 8
    for k, value in enumerate((0x18, 0x4C, 0x64)):
        head[base + 8 * k:base + 8 * k + 8] = ref(value)
    struct.pack_into('>BBBBHH', head, base + 0x18, codec, 1 if looped else 0, nch, 0, rate, 0)
    struct.pack_into('>11I', head, base + 0x20, loop_start, n, data_off + 0x20, blocks, block_size, per_block,
                     last_size, last_samples, last_padded, per_block if adpcm else 0, 4 if adpcm else 0)
    struct.pack_into('>BBH', head, base + 0x4C, 1, 1, 0)
    head[base + 0x50:base + 0x58] = ref(0x58, 1)
    struct.pack_into('>BBHIB', head, base + 0x58, 0x7F, 0x40, 0, 0, nch)
    head[base + 0x64] = nch
    for c in range(nch):
        info_at = 0x78 + 0x38 * c
        head[base + 0x68 + 8 * c:base + 0x68 + 8 * c + 8] = ref(info_at)
        head[base + info_at:base + info_at + 8] = ref(info_at + 8 if adpcm else 0)
        if adpcm:
            struct.pack_into('>16h', head, base + info_at + 8, *coefs_per_channel[c])
            struct.pack_into('>HHhhHhh', head, base + info_at + 0x28, 0, data_per_channel[c][0], 0, 0, 0, 0, 0)
    adpc = bytearray(adpc_size)
    if adpcm:
        adpc[:4] = b'ADPC'
        struct.pack_into('>I', adpc, 4, adpc_size)
        for k in range(blocks):
            for c in range(nch):
                struct.pack_into('>hh', adpc, 8 + 4 * (k * nch + c), *hist[c][k])
    body = bytearray()
    for k in range(blocks):
        size, padded = (last_size, last_padded) if k == blocks - 1 else (block_size, block_size)
        for c in range(nch):
            chunk = data_per_channel[c][k * block_size:k * block_size + size]
            body += chunk + bytes(padded - len(chunk))
    data = bytearray(0x20)
    data[:4] = b'DATA'
    struct.pack_into('>II', data, 4, 0x20 + len(body), 0x18)
    data += body
    header = bytearray(0x40)
    header[:4] = b'RSTM'
    struct.pack_into('>HHIHH6I', header, 4, 0xFEFF, 0x0100, data_off + len(data), 0x40, 3 if adpcm else 2,
                     0x40, head_size, 0x40 + head_size if adpcm else 0, adpc_size, data_off, len(data))
    return bytes(header + head + adpc + data), expected


# ------------------------------------------------------------------------------------------------- voice archive

VOICE_SLOTS = 12                       # a species' clip row: v1 v3 v4 v5 v6 v7 v8 v10 v11 v12 v13 v14
NAME_CALL_SLOT = 10                    # v13, in the shared select file


def voice_id(species, slot):
    """The INFO ID the synthetic voice archive gives species ``species``' clip ``slot`` (what
    ``test_roster_voice_stats_reassign.fill_voice_tables`` writes into the DOL's clip rows)."""
    return species * VOICE_SLOTS + slot


def build_voice_archive(species=43, samples=14, rng=None):
    """An archive shaped like the game's voice banks: per species one family file (``file == species``) holding
    its 11 gameplay clips (RWSD entries in slot order, slot 11 at entry 10) and one shared select file
    (``file == species count``) holding every species' name call (entry = species). Sound INFO IDs are
    ``voice_id``; names ``sp<species>_v<slot>``; one group per species (``group_v<species>``) plus
    ``group_sel_v``. Returns ``(bytes, {(species, slot): RWAV bytes})``."""
    rng = rng or random.Random(77)
    names, sounds, files, groups, rwav_of = [], [], [], [], {}
    select_rwavs = []
    for s in range(species):
        coefs = random_coefs(rng)
        rwavs = []
        for k in range(VOICE_SLOTS):
            rv = build_rwav([(random_adpcm(rng, samples), samples, coefs, 0, 0)], 32000)
            rwav_of[(s, k)] = rv
            if k == NAME_CALL_SLOT:
                select_rwavs.append(rv)
            else:
                rwavs.append(rv)
        files.append({'size': 0, 'wave_size': 0, 'external': '', 'positions': [(s, 0)]})
        groups.append({'name': f'group_v{s:03d}', 'items': [{'file': s, 'data': build_rwsd(list(range(len(rwavs)))),
                                                             'waves': build_rwar(rwavs)}]})
    select = species
    files.append({'size': 0, 'wave_size': 0, 'external': '', 'positions': [(select, 0)]})
    groups.append({'name': 'group_sel_v', 'items': [{'file': select, 'data': build_rwsd(list(range(species))),
                                                     'waves': build_rwar(select_rwavs)}]})
    for s in range(species):
        for k in range(VOICE_SLOTS):
            name = f'sp{s:02d}_v{k}'
            names.append(name)
            if k == NAME_CALL_SLOT:
                file_id, entry, volume = select, s, 127
            else:
                file_id, entry, volume = s, k if k < NAME_CALL_SLOT else k - 1, 107
            sounds.append({'name': name, 'file': file_id, 'player': 0, 'type': 3, 'volume': volume,
                           'priority': 64, 'detail': wave_detail(entry)})
    names += [g['name'] for g in groups] + ['PLAYER_VOICE']
    for f, g in zip(files, groups):
        f['size'], f['wave_size'] = len(g['items'][0]['data']), len(g['items'][0]['waves'])
    players = [{'name': 'PLAYER_VOICE', 'count': 4, 'heap': 0}]
    return build_brsar(names, sounds, [], players, files, groups), rwav_of
