"""Reader for ``MY2.brsar``, the game's NintendoWare sound archive (RSAR 1.4), and the RWSD / RWAR / RWAV files
inside it. Read-only; the layout facts are from the vanilla US archive (2026-10-10).

All big-endian. A *ref* is ``{u8 is_offset, u8 type, u16 pad, u32 value}``; INFO refs are relative to
``INFO + 8``.

- Header ``RSAR``: at 0x10 the SYMB, INFO and FILE section offsets and sizes.
- SYMB: a string table (names of sounds, players, groups, banks); INFO entries name things by index.
- INFO: five table refs at ``base + 8 * n``: 0 sounds, 1 banks, 2 players, 3 files, 4 groups.
  Sound entry: ``{name, file id, player id, ref 3D params, u8 volume, u8 priority, u8 type (1 SEQ, 2 STRM,
  3 WAVE), u8 remote filter, ref detail, ...}``; a WAVE detail starts with the index of the sound's entry in
  its RWSD; a STRM detail with the start position; a SEQ detail with ``{data offset, bank index}``.
  File entry: ``{size, wave size, entry (-1), ref external path, ref position table}``; an external path
  (``stream/x.brstm``) means the bytes live on the disc, else the positions ``{group, item}`` say where in the
  FILE section they are. Group entry: ``{name, entry, ref ext, offset, size, wave offset, wave size, ref
  items}``; item ``{file id, offset, size, wave offset, wave size}`` relative to the group's offsets. A file's
  structure bytes (RWSD) and its wave bytes (RWAR) are stored apart.
- RWSD 1.3: DATA section only; entry -> first note -> wave index. RWAR: TABL of ``{ref, size}`` into its DATA,
  one RWAV per wave. RWAV: INFO ``{u8 format (0 PCM8, 1 PCM16, 2 DSP-ADPCM), u8 loop, u8 channels, u24 rate,
  u32 loop start, u32 loop end (nibbles for ADPCM: the length), u32 channel table, u32 data location}``; per
  channel ``{u32 data offset, u32 ADPCM info offset}``; ADPCM info ``{16 x s16 coefs, gain, ps, h1, h2, loop
  ps, loop h1, loop h2}``.
"""

import os
import struct
from dataclasses import dataclass, field

import numpy as np

from Sound import dsp_adpcm
from Sound.dsp_adpcm import Lane

SEQ, STRM, WAVE = 1, 2, 3
TYPE_NAMES = {SEQ: 'sequence', STRM: 'stream', WAVE: 'wave'}
PCM8, PCM16, ADPCM = 0, 1, 2

TABLE_SOUNDS, TABLE_BANKS, TABLE_PLAYERS, TABLE_FILES, TABLE_GROUPS = range(5)

ARCHIVE = os.path.join('sound_NA', 'MY2.brsar')     # under 1_Input (the user's copy) and 3_Output_Dat (written)


def archive_paths(root_dir):
    """``(1_Input's archive, 3_Output_Dat's archive)`` under the repo root ``root_dir``."""
    return os.path.join(root_dir, '1_Input', ARCHIVE), os.path.join(root_dir, '3_Output_Dat', ARCHIVE)


def u8(b, o): return b[o]
def u16(b, o): return struct.unpack_from('>H', b, o)[0]
def u32(b, o): return struct.unpack_from('>I', b, o)[0]
def s16(b, o): return struct.unpack_from('>h', b, o)[0]


class BrsarError(Exception):
    pass


@dataclass
class Sound:
    index: int                  # the sound's INFO ID (what the game's voice tables hold)
    name: str
    file_id: int
    player_id: int
    type: int
    volume: int
    priority: int
    detail: int                 # absolute offset of the type-specific detail block
    wave_entry: int = None      # WAVE: entry index in the file's RWSD
    bank: int = None            # SEQ: bank index

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.type, f'type {self.type}')


@dataclass
class Player:
    index: int
    name: str
    sound_count: int
    heap_size: int


@dataclass
class Bank:
    index: int
    name: str
    file_id: int


@dataclass
class Item:
    file_id: int
    offset: int
    size: int
    wave_offset: int
    wave_size: int


@dataclass
class Group:
    index: int
    name: str
    offset: int
    size: int
    wave_offset: int
    wave_size: int
    items: list = field(default_factory=list)


@dataclass
class FileEntry:
    index: int
    size: int
    wave_size: int
    external_path: str          # '' for a file stored in the archive
    positions: list             # [(group index, item index)]


@dataclass
class Wave:
    """One RWAV: ``channels`` are ``Lane`` objects (ADPCM) or int16 arrays (PCM); ``loop`` is
    ``(start, end)`` in samples, end exclusive, or None; ``samples`` per channel."""
    format: int
    rate: int
    samples: int
    channels: list
    loop: tuple = None


class Brsar:
    def __init__(self, data):
        self.b = b = bytes(data)
        if b[:4] != b'RSAR':
            raise BrsarError('not a BRSAR file (no RSAR magic)')
        self.symb, self.symb_size, self.info, self.info_size, self.file, self.file_size = struct.unpack_from('>6I', b, 0x10)
        self.base = self.info + 8
        self.names = self._read_names()
        self._tables = {}
        self._sounds = None

    @classmethod
    def from_file(cls, path):
        with open(path, 'rb') as handle:
            return cls(handle.read())

    # ---------------------------------------------------------------- tables
    def _read_names(self):
        s = self.symb + 8
        table = u32(self.b, s)
        count = u32(self.b, s + table)
        names = []
        for i in range(count):
            o = s + u32(self.b, s + table + 4 + 4 * i)
            names.append(self.b[o:self.b.index(b'\0', o)].decode('latin1'))
        return names

    def name(self, index):
        return self.names[index] if 0 <= index < len(self.names) else None

    def ref(self, o):
        """Absolute offset of the INFO ref at ``o``."""
        return self.base + u32(self.b, o + 4)

    def table(self, which):
        """Absolute offsets of the entries of INFO table ``which``."""
        if which not in self._tables:
            t = self.ref(self.base + 8 * which)
            self._tables[which] = [self.ref(t + 4 + 8 * i) for i in range(u32(self.b, t))]
        return self._tables[which]

    def sounds(self):
        if self._sounds is None:
            b = self.b
            out = []
            for i, o in enumerate(self.table(TABLE_SOUNDS)):
                kind = b[o + 0x16]
                detail = self.ref(o + 0x18)
                sound = Sound(index=i, name=self.name(u32(b, o)) or f'sound_{i}', file_id=u32(b, o + 4),
                              player_id=u32(b, o + 8), type=kind, volume=b[o + 0x14], priority=b[o + 0x15],
                              detail=detail)
                if kind == WAVE:
                    sound.wave_entry = u32(b, detail)
                elif kind == SEQ:
                    sound.bank = u32(b, detail + 4)
                out.append(sound)
            self._sounds = out
        return self._sounds

    def players(self):
        b = self.b
        return [Player(i, self.name(u32(b, o)), u32(b, o + 4), u32(b, o + 8))
                for i, o in enumerate(self.table(TABLE_PLAYERS))]

    def banks(self):
        b = self.b
        return [Bank(i, self.name(u32(b, o)), u32(b, o + 0xC)) for i, o in enumerate(self.table(TABLE_BANKS))]

    def groups(self):
        b = self.b
        out = []
        for i, g in enumerate(self.table(TABLE_GROUPS)):
            items_table = self.ref(g + 0x20)
            items = []
            for k in range(u32(b, items_table)):
                item = self.ref(items_table + 4 + 8 * k)
                items.append(Item(*struct.unpack_from('>5I', b, item)))
            out.append(Group(index=i, name=self.name(u32(b, g)), offset=u32(b, g + 0x10), size=u32(b, g + 0x14),
                             wave_offset=u32(b, g + 0x18), wave_size=u32(b, g + 0x1C), items=items))
        return out

    def files(self):
        b = self.b
        out = []
        for i, fo in enumerate(self.table(TABLE_FILES)):
            external = ''
            if b[fo + 0xC]:                                   # ref present: the external path string
                eo = self.ref(fo + 0xC)
                external = b[eo:b.index(b'\0', eo)].decode('latin1')
            positions = []
            if b[fo + 0x14]:
                pos_table = self.ref(fo + 0x14)
                for k in range(u32(b, pos_table)):
                    pos = self.ref(pos_table + 4 + 8 * k)
                    positions.append((u32(b, pos), u32(b, pos + 4)))
            out.append(FileEntry(index=i, size=u32(b, fo), wave_size=u32(b, fo + 4), external_path=external,
                                 positions=positions))
        return out

    # ---------------------------------------------------------------- file bytes
    def file_bytes(self, file_id, groups=None, files=None):
        """``(structure bytes, wave bytes)`` of an archived file, from its first group position; None for an
        external (stream) file."""
        files = files if files is not None else self.files()
        entry = files[file_id]
        if entry.external_path or not entry.positions:
            return None
        groups = groups if groups is not None else self.groups()
        group_index, item_index = entry.positions[0]
        group = groups[group_index]
        item = group.items[item_index]
        if item.file_id != file_id:
            raise BrsarError(f'file {file_id}: group {group_index} item {item_index} belongs to file {item.file_id}')
        start = group.offset + item.offset
        wave_start = group.wave_offset + item.wave_offset
        return self.b[start:start + item.size], self.b[wave_start:wave_start + item.wave_size]


# -------------------------------------------------------------------- RWSD / RWAR / RWAV

def rwsd_entry_count(r):
    if r[:4] != b'RWSD':
        raise BrsarError('not an RWSD file')
    d = u32(r, 0x10) + 8
    return u32(r, d)


def rwsd_wave_index(r, entry):
    """RWSD DATA entry -> index of its wave in the RWAR (entry {ref info, ref tracks, ref notes}; the first
    note's info starts with the wave index; refs relative to DATA + 8)."""
    if r[:4] != b'RWSD':
        raise BrsarError('not an RWSD file')
    d = u32(r, 0x10) + 8
    if entry >= u32(r, d):
        raise BrsarError(f'RWSD entry {entry} of {u32(r, d)}')
    e = d + u32(r, d + 4 + 8 * entry + 4)
    notes = d + u32(r, e + 0x14)
    note = d + u32(r, notes + 8)
    return u32(r, note)


def rwar_count(w):
    if w[:4] != b'RWAR':
        raise BrsarError('not an RWAR file')
    return u32(w, u32(w, 0x10) + 8)


def rwar_wave_bytes(w, index):
    """The RWAV bytes of wave ``index`` of an RWAR."""
    if w[:4] != b'RWAR':
        raise BrsarError('not an RWAR file')
    tabl = u32(w, 0x10)
    data = u32(w, 0x18)
    if index >= u32(w, tabl + 8):
        raise BrsarError(f'RWAR wave {index} of {u32(w, tabl + 8)}')
    entry = tabl + 0xC + 12 * index
    start = data + u32(w, entry + 4)
    return w[start:start + u32(w, entry + 8)]


def parse_rwav(w):
    """An RWAV file -> ``Wave``."""
    if w[:4] != b'RWAV':
        raise BrsarError('not an RWAV file')
    info = u32(w, 0x10)
    wdata = u32(w, 0x18)
    wi = info + 8
    fmt, looped, nch = w[wi], w[wi + 1], w[wi + 2]
    rate = (w[wi + 3] << 16) | u16(w, wi + 4)
    loop_start, loop_end = u32(w, wi + 8), u32(w, wi + 0xC)
    chan_table = wi + u32(w, wi + 0x10)
    if fmt == ADPCM:
        samples = dsp_adpcm.nibbles_to_samples(loop_end)
        loop = (dsp_adpcm.nibble_to_sample(loop_start), samples) if looped else None
    else:
        samples = loop_end
        loop = (loop_start, loop_end) if looped else None
    channels = []
    for c in range(nch):
        ci = wi + u32(w, chan_table + 4 * c)
        start = wdata + 8 + u32(w, ci)
        if fmt == ADPCM:
            adpcm = wi + u32(w, ci + 4)
            coefs = [s16(w, adpcm + 2 * k) for k in range(16)]
            h1, h2 = s16(w, adpcm + 0x24), s16(w, adpcm + 0x26)
            channels.append(Lane(w[start:start + dsp_adpcm.samples_to_bytes(samples)], samples, coefs, h1, h2))
        elif fmt == PCM16:
            channels.append(np.frombuffer(w[start:start + 2 * samples], dtype='>i2').astype(np.int16))
        elif fmt == PCM8:
            channels.append((np.frombuffer(w[start:start + samples], dtype=np.int8).astype(np.int16) << 8))
        else:
            raise BrsarError(f'unknown wave format {fmt}')
    return Wave(format=fmt, rate=rate, samples=samples, channels=channels, loop=loop)


def decode_waves(waves):
    """Decode every ``Wave``'s channels at once; returns ``[[int16 array per channel] per wave]``."""
    lanes, slots = [], []
    for wi, wave in enumerate(waves):
        for ci, channel in enumerate(wave.channels):
            if isinstance(channel, Lane):
                lanes.append(channel)
                slots.append((wi, ci))
    decoded = dsp_adpcm.decode(lanes) if lanes else []
    out = [[np.asarray(c, dtype=np.int16) if not isinstance(c, Lane) else None for c in wave.channels]
           for wave in waves]
    for (wi, ci), samples in zip(slots, decoded):
        out[wi][ci] = samples
    return out
