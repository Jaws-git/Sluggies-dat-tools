"""Add WAVE sounds to ``MY2.brsar``: new clips into an existing RWSD + RWAR file, each with a new sound entry.

The way the external tool (``brsar_write.py``, permission to reuse) and Extra Innings added Rosalina, Luma and
Larry: nothing moves, everything new is appended, so every stock sound keeps its INFO ID and every stock file
its bytes. Per ``add_sounds`` call:

- RWAR of each touched file: rebuilt with the new RWAVs appended (``rwar_split`` / ``rwar_build``). An RWAV
  comes from another file (``sound_rwav``: a stock clip copied byte for byte, the per-character stock voices)
  or from samples (``rwav_build`` / ``encode_clip``: DSP-ADPCM with the coefficients of a stock wave of the
  same file, ``dsp_adpcm.encode``).
- RWSD: rebuilt with one new DATA entry per clip, a relocated copy of entry 0 whose note names the new wave
  (``rwsd_add_entries``).
- INFO: new sound entries (copies of a template sound's entry, detail and 3D-parameter blocks, with the name,
  file id and RWSD entry changed), appended, and the sound table moved to the section's end with the extra
  refs; the touched files' sizes updated.
- SYMB: the names appended, the string table moved to the end. The name-lookup tries are not updated (the
  game plays voices by INFO ID; names serve tools).
- FILE: laid out again, group by group in file order (structure bytes, then wave bytes, 32-byte aligned); the
  group and item offsets and sizes rewritten. A file listed in several groups is written into each of them,
  as the game stores it. Nothing added: the layout reproduces the vanilla bytes (hand-run
  ``probe_brsar_write.py``, 2026-10-10).

With no sounds to add, ``add_sounds`` returns the archive unchanged (SYMB and INFO are rewritten only when
they grow). Stream sounds (external .brstm) are out of scope here.

``replace_waves`` gives existing WAVE sounds new RWAVs (the sound effects, ``Sound/effects``): the touched files'
RWARs rebuilt, the same FILE layout; INFO changes only in the files' sizes and the groups' offsets.
``rwav_from_channels`` builds such an RWAV in a stock wave's layout (mono or stereo, looped or not).
"""

import struct
from dataclasses import dataclass

import numpy as np

from Sound import brsar, dsp_adpcm
from Sound.brsar import ADPCM, WAVE, BrsarError, s16, u16, u32

ALIGN = 0x20
ENTRY_LEN, DETAIL_LEN, PARAMS_LEN = 0x2C, 0x14, 0x0C     # a sound entry, a WAVE detail and its 3D parameters
CANDIDATES = 6                                           # stock waves whose coefficients a new clip may take


class WriteError(BrsarError):
    pass


@dataclass
class NewSound:
    """One sound to add: ``rwav`` (an RWAV file) goes into archived file ``file_id``'s RWAR; the sound entry is a
    copy of ``template``'s (a WAVE sound's INFO ID) with ``name``; ``volume`` replaces the template's when given."""
    name: str
    file_id: int
    rwav: bytes
    template: int
    volume: int = None


def _align(n, a=ALIGN):
    return (n + a - 1) & ~(a - 1)


def _pad(b, a=ALIGN):
    return bytes(b) + bytes(_align(len(b), a) - len(b))


def _p32(v):
    return struct.pack('>I', v)


# ------------------------------------------------------------------------------------------------ RWAR / RWAV

def rwar_split(w):
    """RWAR -> its RWAV blobs."""
    if w[:4] != b'RWAR':
        raise WriteError('not an RWAR file')
    tabl, data = u32(w, 0x10), u32(w, 0x18)
    out = []
    for i in range(u32(w, tabl + 8)):
        e = tabl + 0xC + 12 * i
        off, size = u32(w, e + 4), u32(w, e + 8)
        out.append(w[data + off:data + off + size])
    return out


def rwar_build(rwavs, version=0x0100):
    """RWAVs -> RWAR (TABL of refs, DATA of 32-byte aligned blobs; offsets relative to the DATA chunk)."""
    n = len(rwavs)
    entries = bytearray()
    body = bytearray()
    cursor = 8                                         # the DATA chunk's header
    for rv in rwavs:
        cursor = _align(cursor)
        entries += b'\x01\x00\x00\x00' + _p32(cursor) + _p32(len(rv))
        body += bytes(cursor - 8 - len(body)) + rv
        cursor += len(rv)
    tabl = bytearray(_pad(b'TABL' + _p32(0) + _p32(n) + bytes(entries)))
    struct.pack_into('>I', tabl, 4, len(tabl))
    data = bytearray(_pad(b'DATA' + _p32(0) + bytes(body)))
    struct.pack_into('>I', data, 4, len(data))
    hdr = bytearray(ALIGN)
    hdr[0:4] = b'RWAR'
    struct.pack_into('>HHIHH', hdr, 4, 0xFEFF, version, ALIGN + len(tabl) + len(data), ALIGN, 2)
    struct.pack_into('>IIII', hdr, 0x10, ALIGN, len(tabl), ALIGN + len(tabl), len(data))
    return bytes(hdr + tabl + data)


def rwav_layout(t):
    """A mono ADPCM RWAV's (wave info, channel info, ADPCM info) offsets."""
    if t[:4] != b'RWAV':
        raise WriteError('not an RWAV file')
    wi = u32(t, 0x10) + 8
    if t[wi] != ADPCM or t[wi + 2] != 1:
        raise WriteError('the template must be a mono DSP-ADPCM wave')
    chans = wi + u32(t, wi + 0x10)
    ci = wi + u32(t, chans)
    return wi, ci, wi + u32(t, ci + 4)


def rwav_coefs(template):
    """A mono ADPCM RWAV's 16 predictor coefficients."""
    adpcm = rwav_layout(template)[2]
    return [s16(template, adpcm + 2 * k) for k in range(16)]


def rwav_from(template, rate, samples, encoded):
    """A new mono RWAV with ``template``'s INFO layout (and coefficients) holding ``samples`` samples already
    encoded with them (``encoded``: whole frames, ``dsp_adpcm.encode``). Not looped."""
    t = bytearray(template)
    info = u32(t, 0x10)
    wi, ci, adpcm = rwav_layout(t)
    if len(encoded) < dsp_adpcm.samples_to_bytes(samples):
        raise WriteError(f'{len(encoded)} encoded bytes hold fewer than {samples} samples')
    t[wi + 1] = 0                                                   # no loop
    t[wi + 3] = (rate >> 16) & 0xFF
    struct.pack_into('>H', t, wi + 4, rate & 0xFFFF)
    struct.pack_into('>II', t, wi + 8, 0, dsp_adpcm.samples_to_nibbles(samples))   # loop start, length
    struct.pack_into('>H', t, adpcm + 0x22, encoded[0])             # initial predictor / scale
    struct.pack_into('>hh', t, adpcm + 0x24, 0, 0)                  # history
    head = bytearray(_pad(t[:info + u32(t, 0x14)]))
    data = bytearray(_pad(b'DATA' + _p32(0) + bytes(encoded)))
    struct.pack_into('>I', data, 4, len(data))
    struct.pack_into('>II', head, 0x18, len(head), len(data))
    struct.pack_into('>I', head, 8, len(head) + len(data))
    struct.pack_into('>I', head, ci, 0)                             # channel data offset, relative to DATA + 8
    return bytes(head + data)


def rwav_build(template, rate, samples):
    """A new mono RWAV from ``samples`` (int16) with the template wave's coefficients."""
    x = np.asarray(samples, dtype=np.int64)
    encoded, _ = dsp_adpcm.encode(x, rwav_coefs(template))
    return rwav_from(template, rate, len(x), encoded)


def rwav_error(rv, samples):
    """Squared error of an RWAV's decoded samples against the source."""
    wave = brsar.parse_rwav(rv)
    decoded = brsar.decode_waves([wave])[0][0].astype(np.int64)
    x = np.asarray(samples, dtype=np.int64)[:len(decoded)]
    return int(((x - decoded[:len(x)]) ** 2).sum())


def candidates(rwavs):
    """A file's candidate templates for new clips: its first ``CANDIDATES`` mono ADPCM waves."""
    out = []
    for rv in rwavs:
        wi = u32(rv, 0x10) + 8
        if rv[wi] == ADPCM and rv[wi + 2] == 1:
            out.append(rv)
        if len(out) == CANDIDATES:
            break
    return out


def encode_clip(templates, rate, samples):
    """The RWAV of a clip: encoded with each template's coefficients, the one that reproduces it best wins
    (the first on a tie)."""
    if not templates:
        raise WriteError('no mono ADPCM wave to take coefficients from')
    built = [rwav_build(t, rate, samples) for t in templates]
    return min(built, key=lambda rv: rwav_error(rv, samples))


# ------------------------------------------------------------------------------------------------ RWSD

def _is_ref(b, o, lo, hi):
    return b[o] == 1 and b[o + 2:o + 4] == b'\0\0' and lo <= u32(b, o + 4) < hi


def rwsd_add_entries(r, wave_indexes):
    """Append one DATA entry per wave index, each a relocated copy of entry 0 whose first note names that wave.
    Entries are contiguous blocks whose refs point inside themselves (relative to DATA + 8); every block moves
    by the grown entry table. Returns ``(RWSD bytes, the new entries' indexes)``."""
    if r[:4] != b'RWSD':
        raise WriteError('not an RWSD file')
    data = u32(r, 0x10)
    d = data + 8
    n = u32(r, d)
    if n == 0:
        raise WriteError('the RWSD has no entry to copy')
    starts = [u32(r, d + 4 + 8 * i + 4) for i in range(n)]
    ends = starts[1:] + [u32(r, data + 4) - 8]
    blocks = [bytes(r[d + s:d + e]) for s, e in zip(starts, ends)]
    template, t0 = blocks[0], starts[0]
    note_off = u32(r, d + u32(r, d + t0 + 0x14) + 8)        # entry -> note table -> first note
    ref_pos = [k for k in range(0, len(template) - 7, 4) if _is_ref(template, k, t0, t0 + len(template))]
    new_n = n + len(wave_indexes)
    out_blocks, table = [], []
    cursor = 4 + 8 * new_n
    for s, blk in zip(starts, blocks):
        delta = cursor - s
        blk = bytearray(blk)
        for k in range(0, len(blk) - 7, 4):
            if _is_ref(blk, k, s, s + len(blk)):
                struct.pack_into('>I', blk, k + 4, u32(blk, k + 4) + delta)
        table.append(cursor)
        out_blocks.append(bytes(blk))
        cursor += len(blk)
    for wave in wave_indexes:
        delta = cursor - t0
        blk = bytearray(template)
        for k in ref_pos:
            struct.pack_into('>I', blk, k + 4, u32(blk, k + 4) + delta)
        struct.pack_into('>I', blk, note_off - t0, wave)
        table.append(cursor)
        out_blocks.append(bytes(blk))
        cursor += len(blk)
    sec = bytearray(b'DATA' + _p32(0) + _p32(new_n))
    for off in table:
        sec += b'\x01\x00\x00\x00' + _p32(off)
    for blk in out_blocks:
        sec += blk
    sec = bytearray(_pad(sec))
    struct.pack_into('>I', sec, 4, len(sec))
    hdr = bytearray(r[:ALIGN])
    struct.pack_into('>I', hdr, 8, ALIGN + len(sec))
    struct.pack_into('>II', hdr, 0x10, ALIGN, len(sec))
    return bytes(hdr + sec), list(range(n, new_n))


# ------------------------------------------------------------------------------------------------ archive

def sound_rwav(archive, sound):
    """The RWAV a WAVE sound plays, byte for byte (``sound``: a ``Sound`` or an INFO ID)."""
    if isinstance(sound, int):
        sound = archive.sounds()[sound]
    if sound.type != WAVE:
        raise WriteError(f'{sound.name} is a {sound.type_name} sound, not a wave')
    parts = archive.file_bytes(sound.file_id)
    if parts is None:
        raise WriteError(f'{sound.name}: file {sound.file_id} is not stored in the archive')
    r, w = parts
    return brsar.rwar_wave_bytes(w, brsar.rwsd_wave_index(r, sound.wave_entry))


def file_candidates(archive, file_id):
    """The coefficient templates of an archived file (``candidates`` of its RWAR)."""
    parts = archive.file_bytes(file_id)
    if parts is None:
        raise WriteError(f'file {file_id} is not stored in the archive')
    return candidates(rwar_split(parts[1]))


def add_sounds(data, new):
    """``new``: ``NewSound`` list. Returns ``(archive bytes, the new sounds' INFO IDs)``. With nothing to add,
    the bytes come back unchanged."""
    new = list(new)
    a = brsar.Brsar(data)
    if not new:
        return a.b, []
    b = a.b
    sounds = a.sounds()
    files = a.files()
    groups = a.groups()
    taken = set(a.names)
    for s in new:
        if not s.name or s.name in taken:
            raise WriteError(f'sound name {s.name!r} is empty or taken')
        taken.add(s.name)
        if not 0 <= s.file_id < len(files) or files[s.file_id].external_path or not files[s.file_id].positions:
            raise WriteError(f'{s.name}: file {s.file_id} is not an archived file')
        if s.rwav[:4] != b'RWAV':
            raise WriteError(f'{s.name}: not an RWAV')
        if not 0 <= s.template < len(sounds) or sounds[s.template].type != WAVE:
            raise WriteError(f'{s.name}: template {s.template} is not a wave sound')
        if s.volume is not None and not 0 <= s.volume <= 127:
            raise WriteError(f'{s.name}: volume {s.volume} is not 0-127')

    # 1. every archived file's bytes
    blobs = {}
    for f in files:
        if f.positions and not f.external_path:
            blobs[f.index] = list(a.file_bytes(f.index, groups, files))

    # 2. the new waves into their files
    by_file = {}
    for s in new:
        by_file.setdefault(s.file_id, []).append(s)
    entry_of = {}
    for fid, items in by_file.items():
        r, w = blobs[fid]
        if r[:4] != b'RWSD' or w[:4] != b'RWAR':
            raise WriteError(f'file {fid} is not an RWSD + RWAR file')
        rwavs = rwar_split(w)
        first = len(rwavs)
        rwavs += [s.rwav for s in items]
        w = rwar_build(rwavs, u16(w, 6))
        r, entries = rwsd_add_entries(r, list(range(first, first + len(items))))
        blobs[fid] = [r, w]
        for s, e in zip(items, entries):
            entry_of[s.name] = e

    # 3. SYMB: names appended, the string table moved to the end
    symb, symb_size = a.symb, a.symb_size
    s0 = symb + 8
    old_table = u32(b, s0)
    sym = bytearray(b[symb:symb + symb_size])
    name_index = {}
    str_off = []
    for s in new:
        name_index[s.name] = len(a.names) + len(str_off)
        str_off.append(len(sym) - 8)
        sym += s.name.encode('latin1') + b'\0'
    while len(sym) % 4:
        sym.append(0)
    new_table = len(sym) - 8
    total = len(a.names) + len(new)
    sym += _p32(total)
    for i in range(total):
        sym += _p32(u32(b, s0 + old_table + 4 + 4 * i) if i < len(a.names) else str_off[i - len(a.names)])
    struct.pack_into('>I', sym, 8, new_table)
    sym = bytearray(_pad(sym))
    struct.pack_into('>I', sym, 4, len(sym))

    # 4. INFO: new sound entries, the sound table moved to the end, file sizes
    info, info_size = a.info, a.info_size
    inf = bytearray(b[info:info + info_size])
    base = 8
    sound_offsets = a.table(brsar.TABLE_SOUNDS)
    new_refs = []
    for s in new:
        t = sounds[s.template]
        t_entry = sound_offsets[t.index] - info
        t_detail = t.detail - info
        t_params = a.ref(sound_offsets[t.index] + 0xC) - info
        while len(inf) % 4:
            inf.append(0)
        params = len(inf)
        inf += inf[t_params:t_params + PARAMS_LEN]
        detail = len(inf)
        inf += inf[t_detail:t_detail + DETAIL_LEN]
        struct.pack_into('>I', inf, detail, entry_of[s.name])
        entry = len(inf)
        inf += inf[t_entry:t_entry + ENTRY_LEN]
        struct.pack_into('>II', inf, entry, name_index[s.name], s.file_id)
        if s.volume is not None:
            inf[entry + 0x14] = s.volume
        struct.pack_into('>I', inf, entry + 0xC + 4, params - base)
        struct.pack_into('>I', inf, entry + 0x18 + 4, detail - base)
        new_refs.append(entry - base)
    old_t = u32(inf, base + 4)
    old_n = u32(inf, base + old_t)
    while len(inf) % 4:
        inf.append(0)
    new_t = len(inf)
    inf += _p32(old_n + len(new_refs)) + bytes(inf[base + old_t + 4:base + old_t + 4 + 8 * old_n])
    for r_ in new_refs:
        inf += b'\x01\x00\x00\x00' + _p32(r_)
    struct.pack_into('>I', inf, base + 4, new_t - base)
    file_offsets = a.table(brsar.TABLE_FILES)
    for fid in by_file:
        struct.pack_into('>II', inf, file_offsets[fid] - info, len(blobs[fid][0]), len(blobs[fid][1]))
    inf = bytearray(_pad(inf))
    struct.pack_into('>I', inf, 4, len(inf))
    return _assemble(a, groups, sym, inf, blobs), [old_n + i for i in range(len(new))]


def _assemble(a, groups, sym, inf, blobs):
    """The archive bytes of SYMB ``sym``, INFO ``inf`` (padded; its group and item offsets are rewritten here)
    and every archived file's ``[structure bytes, wave bytes]`` in ``blobs``, by file id."""
    b, base = a.b, 8
    info = a.info
    # 5. FILE: the groups laid out again (in their file order), structure bytes then wave bytes
    file_start = _align(0x40 + len(sym) + len(inf))
    body_base = file_start + ALIGN                     # the FILE chunk header
    body = bytearray()
    group_offsets = a.table(brsar.TABLE_GROUPS)
    for g in sorted(groups, key=lambda g: g.offset):
        go = group_offsets[g.index] - info
        items_table = base + u32(inf, go + 0x24)
        item_offsets = [base + u32(inf, items_table + 4 + 8 * i + 4) for i in range(u32(inf, items_table))]
        g_off = body_base + len(body)
        for io in item_offsets:
            r = blobs[u32(inf, io)][0]
            body += bytes(-len(body) % ALIGN)
            struct.pack_into('>II', inf, io + 4, body_base + len(body) - g_off, len(r))
            body += r
        body += bytes(-len(body) % ALIGN)
        g_size = body_base + len(body) - g_off
        w_off = body_base + len(body)
        for io in item_offsets:
            w = blobs[u32(inf, io)][1]
            body += bytes(-len(body) % ALIGN)
            # an item without wave bytes gets offset 0, as the game's archive writes it
            struct.pack_into('>II', inf, io + 12, body_base + len(body) - w_off if w else 0, len(w))
            body += w
        body += bytes(-len(body) % ALIGN)
        struct.pack_into('>IIII', inf, go + 0x10, g_off, g_size, w_off, body_base + len(body) - w_off)
    fsec = bytearray(b'FILE' + bytes(ALIGN - 4)) + body
    struct.pack_into('>I', fsec, 4, len(fsec))

    # 6. header
    hdr = bytearray(b[:0x40])
    inf_off = 0x40 + len(sym)
    struct.pack_into('>6I', hdr, 0x10, 0x40, len(sym), inf_off, len(inf), file_start, len(fsec))
    out = hdr + sym + inf + bytes(file_start - inf_off - len(inf)) + fsec
    struct.pack_into('>I', out, 8, len(out))
    return bytes(out)


# ------------------------------------------------------------------------------------------------ replacing

def _note_offset(r, entry):
    """Offset of the wave index of RWSD DATA entry ``entry``'s first note (``brsar.rwsd_wave_index``'s walk)."""
    d = u32(r, 0x10) + 8
    e = d + u32(r, d + 4 + 8 * entry + 4)
    notes = d + u32(r, e + 0x14)
    return d + u32(r, notes + 8)


def replace_waves(data, waves):
    """``waves``: ``{INFO ID of a WAVE sound: RWAV bytes}``. Returns the archive bytes with each sound playing
    its new RWAV; every sound keeps its INFO ID and entry, and nothing else changes.

    A wave only that sound plays is replaced in its RWAR; a wave other sounds play too is left to them and the
    new one appended, the sound's RWSD note pointed at it. The touched files' RWARs are rebuilt, the FILE section
    laid out again (``add_sounds``' layout)."""
    a = brsar.Brsar(data)
    if not waves:
        return a.b
    b = a.b
    sounds, files, groups = a.sounds(), a.files(), a.groups()
    by_file = {}
    for sid, rv in waves.items():
        if not 0 <= sid < len(sounds) or sounds[sid].type != WAVE:
            raise WriteError(f'sound {sid} is not a wave sound')
        s = sounds[sid]
        if files[s.file_id].external_path or not files[s.file_id].positions:
            raise WriteError(f'{s.name}: file {s.file_id} is not an archived file')
        if bytes(rv[:4]) != b'RWAV':
            raise WriteError(f'{s.name}: not an RWAV')
        by_file.setdefault(s.file_id, []).append((s, bytes(rv)))
    blobs = {f.index: list(a.file_bytes(f.index, groups, files))
             for f in files if f.positions and not f.external_path}
    for fid, items in by_file.items():
        r, w = blobs[fid]
        if r[:4] != b'RWSD' or w[:4] != b'RWAR':
            raise WriteError(f'file {fid} is not an RWSD + RWAR file')
        r = bytearray(r)
        rwavs = rwar_split(w)
        own = [s for s in sounds if s.type == WAVE and s.file_id == fid]
        entries = [s.wave_entry for s in own]
        users = {}
        for s in own:
            wi = brsar.rwsd_wave_index(r, s.wave_entry)
            users[wi] = users.get(wi, 0) + 1
        for s, rv in items:
            if entries.count(s.wave_entry) > 1:
                raise WriteError(f'{s.name}: its RWSD entry is shared with another sound')
            wi = brsar.rwsd_wave_index(r, s.wave_entry)
            if users[wi] > 1:
                users[wi] -= 1
                rwavs.append(rv)
                users[len(rwavs) - 1] = 1
                struct.pack_into('>I', r, _note_offset(r, s.wave_entry), len(rwavs) - 1)
            else:
                rwavs[wi] = rv
        blobs[fid] = [bytes(r), rwar_build(rwavs, u16(w, 6))]
    inf = bytearray(b[a.info:a.info + a.info_size])
    file_offsets = a.table(brsar.TABLE_FILES)
    for fid in by_file:
        struct.pack_into('>II', inf, file_offsets[fid] - a.info, len(blobs[fid][0]), len(blobs[fid][1]))
    return _assemble(a, groups, bytes(b[a.symb:a.symb + a.symb_size]), inf, blobs)


def loop_nibble(sample):
    """The nibble address of sample ``sample`` (a wave's loop start; 2 for the first sample)."""
    frame, rest = divmod(int(sample), dsp_adpcm.FRAME_SAMPLES)
    return frame * dsp_adpcm.FRAME_NIBBLES + rest + 2


def rwav_from_channels(template, rate, samples, lanes, loop_start=None):
    """A DSP-ADPCM RWAV with ``template``'s INFO layout (its channel count) holding ``samples`` samples per channel.
    ``lanes``: per channel ``(coefs, encoded bytes, (loop ps, loop h1, loop h2))``; ``loop_start``: the loop's first
    sample (the loop runs to the end), or None. Channel data 32-byte aligned, one after the other."""
    t = bytearray(template)
    if t[:4] != b'RWAV':
        raise WriteError('not an RWAV file')
    info = u32(t, 0x10)
    wi = info + 8
    if t[wi] != ADPCM:
        raise WriteError('the template must be a DSP-ADPCM wave')
    if len(lanes) != t[wi + 2]:
        raise WriteError(f'{len(lanes)} channels for a {t[wi + 2]}-channel wave')
    size = dsp_adpcm.samples_to_bytes(samples)
    stride = _align(size)
    t[wi + 1] = 1 if loop_start is not None else 0
    t[wi + 3] = (rate >> 16) & 0xFF
    struct.pack_into('>H', t, wi + 4, rate & 0xFFFF)
    struct.pack_into('>II', t, wi + 8, loop_nibble(loop_start or 0), dsp_adpcm.samples_to_nibbles(samples))
    chans = wi + u32(t, wi + 0x10)
    body = bytearray()
    for c, (coefs, encoded, loop_context) in enumerate(lanes):
        if len(encoded) < size:
            raise WriteError(f'{len(encoded)} encoded bytes hold fewer than {samples} samples')
        ci = wi + u32(t, chans + 4 * c)
        adpcm = wi + u32(t, ci + 4)
        struct.pack_into('>I', t, ci, c * stride)                    # relative to DATA + 8
        struct.pack_into('>16h', t, adpcm, *coefs)
        struct.pack_into('>HHhh', t, adpcm + 0x20, 0, encoded[0], 0, 0)
        struct.pack_into('>Hhh', t, adpcm + 0x28, *(loop_context if loop_start is not None else (0, 0, 0)))
        body += bytes(c * stride - len(body)) + bytes(encoded[:size])
    head = bytearray(_pad(t[:info + u32(t, 0x14)]))
    data = bytearray(_pad(b'DATA' + _p32(0) + bytes(body)))
    struct.pack_into('>I', data, 4, len(data))
    struct.pack_into('>II', head, 0x18, len(head), len(data))
    struct.pack_into('>I', head, 8, len(head) + len(data))
    return bytes(head + data)
