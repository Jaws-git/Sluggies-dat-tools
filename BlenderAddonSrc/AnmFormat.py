"""ANM animation bank: read, write and keyframe reduction.

bpy-free, so it is testable outside Blender. Layout (big-endian):

  header (0x18)  u32 magic, u32 sequence array, u16 bank id (0 in every
                 stock file), u16 sequence count, u16 track count (all
                 sequences), u16 keyframe count (all tracks), u32 user data
                 size, u32 user data pointer (both 0 in stock files)
  sequence (12)  u32 name pointer (0), u32 track array, u16 track count, u16 pad
  track (16)     f32 length, u32 keyframe array, u16 keyframe count,
                 u16 track id, u8 quantize info, u8 type, u8 interpolation,
                 u8 replace-hierarchy flag (1 in stock files)
  keyframe (12)  f32 time, u32 setting bank, u32 interpolation data (0 when
                 the track's interpolations carry none)

Type bits 0-4 are the channels: 0 translation, 1 scale, 2 Euler rotation, 3
quaternion rotation, 4 matrix; stock files use 0, 1 and 3. Bits 5 and 6 are
flags of unknown meaning. A setting bank holds the track's channels in the
order scale, rotation, translation (verified on stock tracks with all three).
Vectors use the track's quantize info; quaternions are always 0x3E (s16,
14 fraction bits), stored x, y, z, w.

Interpolation byte: bits 0-1 translation, bits 2-3 scale, bits 4-6 rotation;
codes 0 none, 1 linear, 2 Bezier, 3 Hermite, 4 SQUAD, 5 SQUAD with ease,
6 slerp. Per channel the interpolation data holds: Bezier two vectors,
Hermite two vectors + two s16 eases, SQUAD two quaternions, SQUAD with ease
two quaternions + two s16 eases (blob sizes verified by tiling every stock
file's data area). Tracks with no channel bits (types 0x20, 0x60) carry
opaque pointer words; their meaning is unknown.

Stock files share identical setting banks and interpolation blobs between
keyframes; the writer does the same.
"""

import json
import math
import struct

ANM_MAGICS = (0x01321AFD, 0x013240DB, 0x01324210)

CH_TRANSLATION = 0
CH_SCALE = 1
CH_QUATERNION = 3
BANK_ORDER = (CH_SCALE, CH_QUATERNION, CH_TRANSLATION)
CHANNEL_MASK = 0x1F
QUAT_QUANT = 0x3E

INTERP_LINEAR = 1
INTERP_SLERP = 6
# Linear translation and scale, slerp rotation: no interpolation data.
REBUILT_INTERP = INTERP_LINEAR | (INTERP_LINEAR << 2) | (INTERP_SLERP << 4)

HEADER_SIZE = 0x18
SEQUENCE_SIZE = 12
TRACK_SIZE = 16
KEYFRAME_SIZE = 12
S16_MAX = 32767
MAX_SHIFT = 14       # stock tracks use 0x30..0x3E


def quant_component_size(quantize_info):
    return 2 if (quantize_info >> 4) in (0, 3) else 4


def channels_of(track_type):
    """Active channels in setting-bank order."""
    return [ch for ch in BANK_ORDER if track_type & (1 << ch)]


def _interp_code(interp, ch):
    if ch == CH_TRANSLATION:
        return interp & 3
    if ch == CH_SCALE:
        return (interp >> 2) & 3
    return (interp >> 4) & 7


def setting_size(track_type, quantize_info):
    vec = 3 * quant_component_size(quantize_info)
    return sum(8 if ch == CH_QUATERNION else vec for ch in channels_of(track_type))


def interp_size(track_type, quantize_info, interp):
    vec = 3 * quant_component_size(quantize_info)
    size = 0
    for ch in channels_of(track_type):
        code = _interp_code(interp, ch)
        if ch == CH_QUATERNION:
            size += {4: 16, 5: 20}.get(code, 0)
        else:
            size += {2: 2 * vec, 3: 2 * vec + 4}.get(code, 0)
    return size


def _read_values(data, pos, count, quantize_info):
    divisor = 1 << (quantize_info & 0x0F)
    if quant_component_size(quantize_info) == 2:
        raw = struct.unpack_from(f'>{count}h', data, pos)
        return [v / divisor for v in raw], pos + 2 * count
    raw = struct.unpack_from(f'>{count}f', data, pos)
    return [v / divisor for v in raw], pos + 4 * count


def decode_settings(blob, track_type, quantize_info):
    """{channel: values}; quaternions as the game's [-w, x, y, z]."""
    settings = {}
    pos = 0
    for ch in channels_of(track_type):
        if ch == CH_QUATERNION:
            (x, y, z, w), pos = _read_values(blob, pos, 4, QUAT_QUANT)
            settings[ch] = [-w, x, y, z]
        else:
            settings[ch], pos = _read_values(blob, pos, 3, quantize_info)
    return settings


def _quantize(value, quantize_info):
    scaled = round(value * (1 << (quantize_info & 0x0F)))
    if not -S16_MAX - 1 <= scaled <= S16_MAX:
        raise ValueError(f"value {value} does not fit quantize info {quantize_info:#04x}")
    return scaled


def encode_settings(settings, track_type, quantize_info):
    """Setting bank bytes; quaternions given as the game's [-w, x, y, z]."""
    if quant_component_size(quantize_info) != 2:
        raise ValueError(f"only s16 quantize infos are written, not {quantize_info:#04x}")
    out = []
    for ch in channels_of(track_type):
        if ch == CH_QUATERNION:
            nw, x, y, z = settings[ch]
            out += [_quantize(v, QUAT_QUANT) for v in (x, y, z, -nw)]
        else:
            out += [_quantize(v, quantize_info) for v in settings[ch]]
    return struct.pack(f'>{len(out)}h', *out)


def vector_quant(values):
    """s16 quantize info with the most fraction bits that holds *values*."""
    peak = max((abs(v) for v in values), default=0.0)
    for shift in range(MAX_SHIFT, -1, -1):
        if round(peak * (1 << shift)) <= S16_MAX:
            return 0x30 | shift
    raise ValueError(f"value {peak} is too large for an s16 track")


# ---------------------------------------------------------------- reading

def parse(data):
    """Whole bank as plain data.

    {'magic', 'bank_id', 'sequences': [[track, ...], ...]}; track =
    {'track_id', 'length', 'quant', 'type', 'interp', 'replace', 'keys'};
    key = {'time', 'settings' ({channel: values}), 'set_blob', 'interp_blob',
    'set_word', 'interp_word'}. Blobs are the referenced bytes for tracks
    with channels; channel-less tracks keep their raw pointer words, and
    'set_blob' holds the bytes up to the next referenced block when the
    word points into the file (the size is unknown, so this is a guess).
    """
    magic, seq_ptr, bank_id, seq_cnt = struct.unpack_from('>IIHH', data, 0)
    if magic not in ANM_MAGICS:
        raise ValueError(f"Not an ANM file: magic {magic:#010x}")

    raw_tracks = []
    for si in range(seq_cnt):
        _name, trk_ptr, trk_cnt, _pad = struct.unpack_from('>IIHH', data, seq_ptr + SEQUENCE_SIZE * si)
        tracks = []
        for ti in range(trk_cnt):
            length, kf_ptr, kf_cnt, track_id, quant, ttype, interp, replace = struct.unpack_from(
                '>fIHHBBBB', data, trk_ptr + TRACK_SIZE * ti)
            keys = [struct.unpack_from('>fII', data, kf_ptr + KEYFRAME_SIZE * ki) for ki in range(kf_cnt)]
            tracks.append((track_id, length, quant, ttype, interp, replace, keys))
        raw_tracks.append(tracks)

    block_starts = None

    def opaque_blob(ptr):
        nonlocal block_starts
        if block_starts is None:
            block_starts = sorted({p for tracks in raw_tracks for t in tracks
                                   for _, s, i in t[6] for p in (s, i) if 0 < p < len(data)}
                                  | {len(data)})
        end = next((p for p in block_starts if p > ptr), len(data))
        return bytes(data[ptr:min(end, ptr + 256)])

    sequences = []
    for tracks in raw_tracks:
        seq = []
        for track_id, length, quant, ttype, interp, replace, keys in tracks:
            ssize = setting_size(ttype, quant)
            isize = interp_size(ttype, quant, interp)
            out_keys = []
            for time, set_ptr, int_ptr in keys:
                key = {'time': time, 'set_word': set_ptr, 'interp_word': int_ptr}
                if ttype & CHANNEL_MASK:
                    # A null pointer stays null (some stock keys have one).
                    key['set_blob'] = bytes(data[set_ptr:set_ptr + ssize]) if set_ptr else None
                    key['interp_blob'] = (bytes(data[int_ptr:int_ptr + isize]) if int_ptr else None) if isize else b''
                    key['settings'] = (decode_settings(key['set_blob'], ttype, quant)
                                       if key['set_blob'] is not None else {})
                else:
                    key['set_blob'] = opaque_blob(set_ptr) if 0 < set_ptr < len(data) else None
                    key['interp_blob'] = None
                    key['settings'] = {}
                out_keys.append(key)
            seq.append({'track_id': track_id, 'length': length, 'quant': quant, 'type': ttype,
                        'interp': interp, 'replace': replace, 'keys': out_keys})
        sequences.append(seq)
    return {'magic': magic, 'bank_id': bank_id, 'sequences': sequences}


def signature(data):
    """(magic, [track-id set per sequence]) of an ANM file."""
    magic, seq_ptr, _bank, seq_cnt = struct.unpack_from('>IIHH', data, 0)
    if magic not in ANM_MAGICS:
        raise ValueError(f"Not an ANM file: magic {magic:#010x}")
    seqs = []
    for si in range(seq_cnt):
        _name, trk_ptr, trk_cnt, _pad = struct.unpack_from('>IIHH', data, seq_ptr + SEQUENCE_SIZE * si)
        seqs.append({struct.unpack_from('>H', data, trk_ptr + TRACK_SIZE * ti + 10)[0]
                     for ti in range(trk_cnt)})
    return magic, seqs


# ---------------------------------------------------------------- writing

class _Pool:
    """Deduplicated, 2-aligned data blocks; offsets relative to the pool."""

    def __init__(self):
        self.data = bytearray()
        self.offsets = {}

    def add(self, blob):
        if blob in self.offsets:
            return self.offsets[blob]
        if len(self.data) % 2:
            self.data.append(0)
        self.offsets[blob] = len(self.data)
        self.data += blob
        return self.offsets[blob]


def _key_ref(key, pool, blob_name, word_name):
    blob = key.get(blob_name)
    if blob is None:
        return ('raw', key.get(word_name, 0))
    if not blob:
        return ('raw', 0)
    return (blob_name, pool.add(blob))


def write(magic, bank_id, sequences):
    """Bytes of an ANM bank.

    sequences: [[track, ...], ...]; track = {'track_id', 'length', 'quant',
    'type', 'interp', 'replace', 'keys'}; key = {'time', 'set_blob',
    'interp_blob', 'set_word', 'interp_word'}. A blob of None keeps the raw
    word (channel-less tracks), an empty blob writes 0, any other blob is
    pooled and the word becomes its offset. Identical setting banks,
    interpolation blobs and keyframe arrays are stored once.
    """
    tracks = [t for seq in sequences for t in seq]
    key_total = sum(len(t['keys']) for t in tracks)
    if len(sequences) > 0xFFFF or len(tracks) > 0xFFFF or key_total > 0xFFFF:
        raise ValueError(f"too many sequences/tracks/keyframes "
                         f"({len(sequences)}/{len(tracks)}/{key_total})")

    settings, interps = _Pool(), _Pool()
    arrays = {}                     # keyframe array -> offset in the key area
    track_arrays = []
    key_area = 0
    for track in tracks:
        arr = tuple((float(k['time']),
                     _key_ref(k, settings, 'set_blob', 'set_word'),
                     _key_ref(k, interps, 'interp_blob', 'interp_word')) for k in track['keys'])
        if arr and arr not in arrays:
            arrays[arr] = key_area
            key_area += KEYFRAME_SIZE * len(arr)
        track_arrays.append(arr)

    seq_off = HEADER_SIZE
    track_off = seq_off + SEQUENCE_SIZE * len(sequences)
    key_off = track_off + TRACK_SIZE * len(tracks)
    set_off = key_off + key_area
    interp_off = set_off + len(settings.data)
    interp_off += interp_off % 2
    bases = {'set_blob': set_off, 'interp_blob': interp_off}

    out = bytearray(struct.pack('>IIHHHHII', magic, seq_off, bank_id, len(sequences),
                                len(tracks), key_total, 0, 0))
    cursor = track_off
    for seq in sequences:
        out += struct.pack('>IIHH', 0, cursor if seq else 0, len(seq), 0)
        cursor += TRACK_SIZE * len(seq)
    for track, arr in zip(tracks, track_arrays):
        out += struct.pack('>fIHHBBBB', float(track['length']), key_off + arrays[arr] if arr else 0,
                           len(arr), track['track_id'], track['quant'], track['type'],
                           track['interp'], track['replace'])
    for arr in arrays:              # insertion order = offset order
        for time, sref, iref in arr:
            out += struct.pack('>fII', time,
                               *(value if kind == 'raw' else bases[kind] + value
                                 for kind, value in (sref, iref)))
    out += settings.data
    out += bytes(interp_off - len(out))
    out += interps.data
    return bytes(out)


# ---------------------------------------------------------------- rebuilt tracks

def _lerp(a, b, t):
    return [x + (y - x) * t for x, y in zip(a, b)]


def _slerp(a, b, t):
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0.0:
        b = [-v for v in b]
        dot = -dot
    if dot > 0.9995:
        r = _lerp(a, b, t)
    else:
        theta = math.acos(min(1.0, dot))
        s = math.sin(theta)
        wa = math.sin((1 - t) * theta) / s
        wb = math.sin(t * theta) / s
        r = [wa * x + wb * y for x, y in zip(a, b)]
    n = math.sqrt(sum(v * v for v in r)) or 1.0
    return [v / n for v in r]


def reduce_samples(samples, vec_tol, quat_tol):
    """Indices of the samples to keep so that linear/slerp interpolation
    between kept ones reproduces every sample within tolerance.

    samples: [(time, scale, quat, translation)]; quat as [w, x, y, z]. The
    first and last samples are always kept.
    """
    n = len(samples)
    if n <= 2:
        return list(range(n))

    def fits(a, b):
        ta, tb = samples[a][0], samples[b][0]
        for i in range(a + 1, b):
            t = (samples[i][0] - ta) / (tb - ta)
            for ch, tol in ((1, vec_tol), (3, vec_tol)):
                guess = _lerp(samples[a][ch], samples[b][ch], t)
                if max(abs(g - v) for g, v in zip(guess, samples[i][ch])) > tol:
                    return False
            guess = _slerp(samples[a][2], samples[b][2], t)
            q = samples[i][2]
            if sum(g * v for g, v in zip(guess, q)) < 0:
                q = [-v for v in q]
            if max(abs(g - v) for g, v in zip(guess, q)) > quat_tol:
                return False
        return True

    keep = [0]
    anchor = 0
    end = 1
    while end < n - 1:
        if fits(anchor, end + 1):
            end += 1
        else:
            keep.append(end)
            anchor = end
            end += 1
    keep.append(n - 1)
    return keep


def rebuilt_track(track_id, length, samples, channels, flags=0, replace=1,
                  vec_tol=None, quat_tol=2e-4):
    """Track dict for write() from sampled game-local SRTs.

    samples: [(time, scale xyz, quaternion [w, x, y, z], translation xyz)];
    channels: the channels to store (others hold the bone's rest value).
    flags: type bits 5-7 to keep from the original track.
    """
    channels = [ch for ch in BANK_ORDER if ch in channels]
    vec_values = [v for s in samples for ch, idx in ((CH_SCALE, 1), (CH_TRANSLATION, 3))
                  if ch in channels for v in s[idx]]
    quant = vector_quant(vec_values) if vec_values else QUAT_QUANT
    if vec_tol is None:
        vec_tol = 1.0 / (1 << (quant & 0x0F))

    # Keep consecutive quaternions in one hemisphere.
    fixed = []
    prev = None
    for time, scale, quat, trans in samples:
        if prev is not None and sum(a * b for a, b in zip(prev, quat)) < 0:
            quat = [-v for v in quat]
        prev = quat
        fixed.append((time, list(scale), list(quat), list(trans)))
    if CH_QUATERNION not in channels:
        fixed = [(t, s, [1.0, 0.0, 0.0, 0.0], tr) for t, s, _q, tr in fixed]
    if CH_SCALE not in channels:
        fixed = [(t, [0.0] * 3, q, tr) for t, _s, q, tr in fixed]
    if CH_TRANSLATION not in channels:
        fixed = [(t, s, q, [0.0] * 3) for t, s, q, _tr in fixed]

    ttype = (flags & ~CHANNEL_MASK & 0xFF) | sum(1 << ch for ch in channels)
    keys = []
    for idx in reduce_samples(fixed, vec_tol, quat_tol):
        time, scale, quat, trans = fixed[idx]
        w, x, y, z = quat
        settings = {CH_SCALE: scale, CH_QUATERNION: [-w, x, y, z], CH_TRANSLATION: trans}
        keys.append({'time': float(time),
                     'set_blob': encode_settings(settings, ttype, quant) if channels else None,
                     'interp_blob': b'',
                     'set_word': 0, 'interp_word': 0})
    return {'track_id': track_id, 'length': float(length), 'quant': quant, 'type': ttype,
            'interp': REBUILT_INTERP, 'replace': replace, 'keys': keys}


# ---------------------------------------------------------------- import metadata

def track_records(sequence, rebuilt_ids):
    """JSON-ready original track list of one sequence. Tracks whose id is in
    *rebuilt_ids* (and that have channels) are rebuilt from the armature on
    export and keep only their header fields; the rest carry their data."""
    records = []
    for t in sequence:
        rec = {'id': t['track_id'], 'length': t['length'], 'quant': t['quant'],
               'type': t['type'], 'interp': t['interp'], 'replace': t['replace']}
        if not (t['type'] & CHANNEL_MASK and t['track_id'] in rebuilt_ids):
            rec['keys'] = [[k['time'],
                            k['set_blob'].hex() if k['set_blob'] is not None else k['set_word'],
                            k['interp_blob'].hex() if k['interp_blob'] is not None else k['interp_word']]
                           for k in t['keys']]
        records.append(rec)
    return records


def passthrough_track(record):
    """write() track from a track_records() entry that carries data."""
    keys = []
    for time, set_field, int_field in record['keys']:
        key = {'time': time}
        if isinstance(set_field, str):
            key['set_blob'] = bytes.fromhex(set_field)
        else:
            key['set_blob'], key['set_word'] = None, set_field
        if isinstance(int_field, str):
            key['interp_blob'] = bytes.fromhex(int_field)
        else:
            key['interp_blob'], key['interp_word'] = None, int_field
        keys.append(key)
    return {'track_id': record['id'], 'length': record['length'], 'quant': record['quant'],
            'type': record['type'], 'interp': record['interp'], 'replace': record['replace'],
            'keys': keys}


def dumps_records(records):
    return json.dumps(records, separators=(',', ':')).encode('utf-8')


def loads_records(raw):
    return json.loads(raw.decode('utf-8'))
