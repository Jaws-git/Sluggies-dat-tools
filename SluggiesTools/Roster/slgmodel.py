"""Character files of the Sluggers Characters patcher (``<name>.slgmodel``): the reader.

A ``.slgmodel`` is a zip (``format`` 1, at most ``MAX_FILE`` bytes in all) that carries one custom character the
way that patcher shares them: finished game model blocks, portraits and voice clips, plus a glTF copy of the model
for Blender. It is the equivalent of a High + Low ``.sluggie`` pair whose blocks are already built::

    character.json   {"format": 1, "name", "base": the stock character it is built on (its English name, e.g.
                      "Daisy", "Red Shy Guy"), "author", "from", "notes", "left_out": [what the file does not
                      carry, in words], "glb_sha1": model.glb's hash when it was exported, "blocks": [0] or
                      [0, 1], "voice": bool, "voice_slots": {slot: "own" | "base"}}
    models/<N>.slgd  model file N of the character's directory (0 = High, 1 = Low) as a delta against the clean
                     game (below): no game bytes travel in the file
    model.glb        the same model as glTF 2.0 on the base's skeleton (what people open in Blender). While its
                     SHA-1 still equals ``glb_sha1`` the blocks are the model; an edited glb is not read here
    side.png, front.png   the select-screen portraits (48x51)
    voice/<slot>.wav the character's own voice clips, slots ``v1 v3 v4 v5 v6 v7 v8 v10 v11 v12 v13 v14`` (the same
                     12 moments as ``Sound/sound_packs.MOMENTS``, in that order); a slot marked "base" in
                     ``voice_slots`` plays the base character's clip

**Delta (``SLGD1``):** zlib of ``b"SLGD1" | u32 length | sha1 (20 bytes) | ops``. Op 0 (COPY): varint source
index, varint offset, varint length; the sources, in order, are the clean game's ``main.dol``, ``dt_na.dat`` and
``sound_NA/MY2.brsar`` (``DELTA_SOURCES``, under ``1_Input`` here). Op 1 (LITERAL): varint length, then the
bytes. The rebuilt file must have the stated length and SHA-1; a modded ``1_Input`` fails that check
(``SlgError`` names the game files the delta read).

``read(path, input_dir)`` gives a ``SlgModel``: the metadata, the rebuilt blocks, the portraits as RGBA images and
the voice WAVs as bytes. It refuses, in plain words, a damaged or oversized zip, a member outside the format, a
newer ``format``, a ``base`` outside the stock players (``BASE_NAMES``: the patcher's own spelling of the 0x65
stock names, copied as data) and an edited ``model.glb``. Nothing here touches the game files; the planner
(``slot_plan.plan_slgmodel``) decides what the character becomes in the roster.
"""

from __future__ import annotations

import hashlib
import io
import json
import mmap
import os
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field

try:
    from . import ids
except ImportError:
    import ids

FORMAT = 1
SUFFIX = '.slgmodel'
MAX_FILE = 64 << 20                                     # the zip, and its members together
MAX_BLOCK = 16 << 20                                    # one rebuilt model block
META = 'character.json'
GLB = 'model.glb'
HIGH_FILE, LOW_FILE = 0, 1
VIEWS = ('side', 'front')
PORTRAIT_SIZE = (48, 51)
VOICE_SLOTS = ('v1', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v10', 'v11', 'v12', 'v13', 'v14')
MEMBER_RE = re.compile(r'^(character\.json|models/\d+\.slgd|model\.glb|side\.png|front\.png|voice/('
                       + '|'.join(VOICE_SLOTS) + r')\.wav)$')

DELTA_MAGIC = b'SLGD1'
DELTA_HEAD = len(DELTA_MAGIC) + 4 + 20
# the clean game files a delta copies from, in the delta's source order (relative to 1_Input)
DELTA_SOURCES = ('main.dol', 'dt_na.dat', os.path.join('sound_NA', 'MY2.brsar'))
DELTA_SOURCE_NAMES = ('main.dol', 'dt_na.dat', 'sound_NA/MY2.brsar')

# The patcher's stock character names (its ``sluggers_data.CHAR_NAMES``), index = character ID. ``base`` in a
# character file is one of these; only players (below ``ids.PLAYER_END``) can be a base.
BASE_NAMES = (
    'Mario', 'Luigi', 'Donkey Kong', 'Diddy Kong', 'Peach', 'Daisy', 'Green Yoshi', 'Baby Mario', 'Baby Luigi',
    'Bowser', 'Wario', 'Waluigi', 'Green Koopa Troopa', 'Red Toad', 'Boo', 'Toadette', 'Red Shy Guy', 'Birdo',
    'Monty Mole', 'Bowser Jr.', 'Red Koopa Paratroopa', 'Blue Pianta', 'Red Pianta', 'Yellow Pianta', 'Blue Noki',
    'Red Noki', 'Green Noki', 'Hammer Bro', 'Toadsworth', 'Blue Toad', 'Yellow Toad', 'Green Toad', 'Purple Toad',
    'Blue Magikoopa', 'Red Magikoopa', 'Green Magikoopa', 'Yellow Magikoopa', 'King Boo', 'Petey Piranha',
    'Dixie Kong', 'Goomba', 'Paragoomba', 'Red Koopa Troopa', 'Green Koopa Paratroopa', 'Blue Shy Guy',
    'Yellow Shy Guy', 'Green Shy Guy', 'Gray Shy Guy', 'Gray Dry Bones', 'Green Dry Bones', 'Dark Bones',
    'Blue Dry Bones', 'Fire Bro', 'Boomerang Bro', 'Wiggler', 'Blooper', 'Funky Kong', 'Tiny Kong', 'Green Kritter',
    'Blue Kritter', 'Red Kritter', 'Brown Kritter', 'King K. Rool', 'Baby Peach', 'Baby Daisy', 'Baby DK',
    'Red Yoshi', 'Blue Yoshi', 'Yellow Yoshi', 'Light Blue Yoshi', 'Pink Yoshi', 'Unused Yoshi 2', 'Unused Yoshi',
    'Unused Toad', 'Unused Pianta', 'Unused Kritter', 'Unused Koopa', 'Red Mii (M)', 'Orange Mii (M)',
    'Yellow Mii (M)', 'Light Green Mii (M)', 'Green Mii (M)', 'Blue Mii (M)', 'Light Blue Mii (M)', 'Pink Mii (M)',
    'Purple Mii (M)', 'Brown Mii (M)', 'White Mii (M)', 'Black Mii (M)', 'Red Mii (F)', 'Orange Mii (F)',
    'Yellow Mii (F)', 'Light Green Mii (F)', 'Green Mii (F)', 'Blue Mii (F)', 'Light Blue Mii (F)', 'Pink Mii (F)',
    'Purple Mii (F)', 'Brown Mii (F)', 'White Mii (F)', 'Black Mii (F)',
)
assert len(BASE_NAMES) == ids.STOCK_IDS


class SlgError(ValueError):
    """A character file that cannot be used; the text is for the user."""


def _key(name: str) -> str:
    return ' '.join(str(name).split()).lower()


_BASE_IDS = {_key(name): cid for cid, name in enumerate(BASE_NAMES)}


def base_id(name) -> int | None:
    """The stock player ID the base name ``name`` means (case and spacing do not matter), or None when it is not a
    stock player's name (Miis and unknown names)."""
    cid = _BASE_IDS.get(_key(name or ''))
    return cid if cid is not None and cid < ids.PLAYER_END else None


def base_name(cid: int) -> str:
    """The patcher's name of stock character ``cid`` (what an exported file's ``base`` says)."""
    return BASE_NAMES[cid]


# --------------------------------------------------------------------------
# Deltas
# --------------------------------------------------------------------------

def _raw(delta: bytes, what: str) -> bytes:
    try:
        raw = zlib.decompress(delta)
    except zlib.error as exc:
        raise SlgError(f'{what} is damaged (not a delta file: {exc})') from exc
    if len(raw) < DELTA_HEAD or raw[:len(DELTA_MAGIC)] != DELTA_MAGIC:
        raise SlgError(f'{what} is damaged (not a delta file)')
    return raw


def delta_info(delta: bytes, what: str = 'the delta') -> tuple[int, str]:
    """``(length, sha1 hex)`` of the file a delta rebuilds."""
    raw = _raw(delta, what)
    return struct.unpack('>I', raw[5:9])[0], raw[9:29].hex()


def _ops(raw: bytes, what: str):
    """The ops of a decompressed delta: ``('copy', source, offset, length)`` / ``('literal', bytes)``."""
    p = DELTA_HEAD

    def varint():
        nonlocal p
        value = shift = 0
        while True:
            if p >= len(raw):
                raise SlgError(f'{what} is damaged (a delta op is cut short)')
            b = raw[p]
            p += 1
            value |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return value
    while p < len(raw):
        op = raw[p]
        p += 1
        if op == 0:
            yield ('copy', varint(), varint(), varint())
        elif op == 1:
            length = varint()
            if p + length > len(raw):
                raise SlgError(f'{what} is damaged (a delta literal is cut short)')
            yield ('literal', raw[p:p + length])
            p += length
        else:
            raise SlgError(f'{what} is damaged (unknown delta op {op})')


def delta_sources(delta: bytes, what: str = 'the delta') -> list[int]:
    """The source indexes (``DELTA_SOURCES`` order) the delta copies from."""
    raw = _raw(delta, what)
    return sorted({op[1] for op in _ops(raw, what) if op[0] == 'copy'})


def apply_delta(delta: bytes, sources, what: str = 'the delta') -> bytes:
    """The file a delta rebuilds. ``sources``: the clean game files in ``DELTA_SOURCES`` order (anything sliceable
    into bytes: bytes, memoryviews, mmaps), or a callable ``index -> such a buffer`` (opened on first use). A
    rebuilt file with another length or SHA-1 raises ``SlgError`` (a modded game)."""
    raw = _raw(delta, what)
    length, sha = struct.unpack('>I', raw[5:9])[0], raw[9:29]
    if length > MAX_BLOCK:
        raise SlgError(f'{what} would be {length:,} bytes, bigger than any model this reads ({MAX_BLOCK:,})')
    opened = {}

    def source(index):
        if index not in opened:
            try:
                opened[index] = sources(index) if callable(sources) else sources[index]
            except IndexError:
                raise SlgError(f'{what} is damaged (it copies from game file {index}, which does not exist)')
        return opened[index]
    out = bytearray()
    for op in _ops(raw, what):
        if op[0] == 'copy':
            _kind, index, offset, n = op
            src = source(index)
            piece = src[offset:offset + n]
            if len(piece) != n:
                raise SlgError(f'{what} copies {n:,} bytes at {offset:,} of {DELTA_SOURCE_NAMES[index] if index < 3 else index}, '
                               'past its end: 1_Input is not the clean game this file was made from')
            out += piece
        else:
            out += op[1]
        if len(out) > length:
            break
    out = bytes(out)
    if len(out) != length or hashlib.sha1(out).digest() != sha:
        used = [DELTA_SOURCE_NAMES[i] for i in delta_sources(delta, what) if i < len(DELTA_SOURCE_NAMES)]
        raise SlgError(f'{what} does not rebuild: 1_Input is not the clean game this file was made from (it copies '
                       f'from {", ".join(used) or "no game file"}; a modded copy of those files cannot be used)')
    return out


class InputSources:
    """The clean game files under ``input_dir`` as read-only memory maps, opened on first use (a context
    manager; ``self(index)`` is what ``apply_delta`` calls)."""

    def __init__(self, input_dir: str):
        self.input_dir = input_dir
        self._open = {}

    def __call__(self, index: int):
        if index not in self._open:
            if not 0 <= index < len(DELTA_SOURCES):
                raise IndexError(index)
            path = os.path.join(self.input_dir, DELTA_SOURCES[index])
            if not os.path.isfile(path):
                raise SlgError(f'{DELTA_SOURCE_NAMES[index]} is missing in 1_Input: the character file is rebuilt '
                               'from the clean game files'
                               + (' (copy the game\'s files/sound_NA folder there)' if index == 2 else ''))
            handle = open(path, 'rb')
            try:
                view = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            except (OSError, ValueError):                 # an empty file cannot be mapped
                view = handle.read()
            self._open[index] = (handle, view)
        return self._open[index][1]

    def close(self):
        for handle, view in self._open.values():
            if isinstance(view, mmap.mmap):
                view.close()
            handle.close()
        self._open.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


# --------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------

@dataclass
class SlgModel:
    path: str
    name: str
    base: str                                           # the base's name as the file says it
    base_id: int                                        # the stock player it means
    author: str = ''
    source: str = ''                                    # the file's "from" (where the model comes from)
    notes: str = ''
    left_out: list = field(default_factory=list)        # what the file does not carry, in the author's words
    blocks: dict = field(default_factory=dict)          # {0: High block, 1: Low block} as rebuilt
    portraits: dict = field(default_factory=dict)       # {'side' / 'front': RGBA image}
    voice: dict = field(default_factory=dict)           # {slot: WAV bytes} the character's own clips
    voice_slots: dict = field(default_factory=dict)     # {slot: 'own' | 'base'}
    has_glb: bool = False
    glb_edited: bool = False

    @property
    def file_name(self) -> str:
        return os.path.basename(self.path)

    @property
    def high(self) -> bytes:
        return self.blocks[HIGH_FILE]

    @property
    def low(self) -> bytes | None:
        return self.blocks.get(LOW_FILE)

    @property
    def high_only(self) -> bool:
        """No Low model of its own: the High model doubles as the Low model (as ``--as-low``)."""
        return self.low is None


def _text(meta: dict, key: str) -> str:
    value = meta.get(key)
    return value.strip() if isinstance(value, str) else ''


def read_meta(path: str) -> tuple[dict, zipfile.ZipFile]:
    """``(character.json, the open zip)`` of the file at ``path``, after the zip checks (size, members)."""
    name = os.path.basename(path)
    try:
        if os.path.getsize(path) > MAX_FILE:
            raise SlgError(f'{name} is too big to be a character file (over {MAX_FILE >> 20} MB)')
        zf = zipfile.ZipFile(path)
    except OSError as exc:
        raise SlgError(f'{name} cannot be read: {exc.strerror or exc}') from exc
    except zipfile.BadZipFile:
        raise SlgError(f'{name} is not a character file (.slgmodel) or is damaged') from None
    infos = zf.infolist()
    if sum(i.file_size for i in infos) > MAX_FILE:
        raise SlgError(f'{name} is too big to be a character file (over {MAX_FILE >> 20} MB unpacked)')
    for info in infos:
        if info.is_dir():
            continue
        if not MEMBER_RE.match(info.filename):
            raise SlgError(f'{name} is not a character file: it holds {info.filename!r}, which the format has no '
                           'place for')
    try:
        meta = json.loads(zf.read(META).decode('utf-8'))
    except KeyError:
        raise SlgError(f'{name} is not a character file: no {META} inside') from None
    except (ValueError, UnicodeDecodeError) as exc:
        raise SlgError(f'{name}: {META} is damaged ({exc})') from exc
    if not isinstance(meta, dict):
        raise SlgError(f'{name}: {META} is not a character description')
    if meta.get('format') != FORMAT:
        raise SlgError(f'{name} is a character file of format {meta.get("format")!r}; this tool reads format '
                       f'{FORMAT} (a newer patcher wrote it)')
    return meta, zf


def read(path: str, input_dir: str, blocks: bool = True) -> SlgModel:
    """The character file at ``path`` (module docstring). ``input_dir``: ``1_Input`` (the deltas' clean game
    files). ``blocks`` False reads the metadata, portraits and voice only."""
    from PIL import Image
    path = os.path.abspath(path)
    name = os.path.basename(path)
    meta, zf = read_meta(path)
    with zf:
        names = set(zf.namelist())
        title = _text(meta, 'name')
        if not title:
            raise SlgError(f'{name}: the character has no name')
        base = _text(meta, 'base')
        cid = base_id(base)
        if cid is None:
            raise SlgError(f'{name} is built on {base!r}, which is not a stock player this tool knows (the base '
                           'names are the patcher\'s English names, e.g. "Daisy", "Red Shy Guy")')
        indexes = meta.get('blocks')
        if not isinstance(indexes, list) or not indexes or any(not isinstance(i, int) for i in indexes):
            raise SlgError(f'{name}: {META} names no model files ("blocks")')
        indexes = sorted(set(indexes))
        if indexes not in ([HIGH_FILE], [HIGH_FILE, LOW_FILE]):
            raise SlgError(f'{name} carries model files {indexes}; this tool reads the High model (0) and its Low '
                           'model (1) only')
        for i in indexes:
            if f'models/{i}.slgd' not in names:
                raise SlgError(f'{name} is damaged (no models/{i}.slgd)')
        glb_sha = meta.get('glb_sha1')
        has_glb = GLB in names
        glb_edited = has_glb and glb_sha not in (None, hashlib.sha1(zf.read(GLB)).hexdigest())
        if glb_edited:
            raise SlgError(f'{name}: its model.glb was edited in Blender after the file was exported, so its game '
                           'model is out of date. This tool uses the finished model blocks only (reading the '
                           '.glb is not supported yet): export the character again with the patcher first.')
        left_out = meta.get('left_out') or []
        left_out = [left_out] if isinstance(left_out, str) else left_out
        model = SlgModel(path, title, base, cid, _text(meta, 'author'), _text(meta, 'from'), _text(meta, 'notes'),
                         [str(x).strip() for x in left_out if str(x).strip()], has_glb=has_glb,
                         glb_edited=glb_edited)
        slots = meta.get('voice_slots') if isinstance(meta.get('voice_slots'), dict) else {}
        model.voice_slots = {s: ('own' if str(slots.get(s, 'base')).lower() == 'own' else 'base') for s in VOICE_SLOTS}
        for view in VIEWS:
            member = f'{view}.png'
            if member not in names:
                continue
            try:
                with Image.open(io.BytesIO(zf.read(member))) as img:
                    img.load()
                    model.portraits[view] = img.convert('RGBA')
            except Exception as exc:                      # PIL raises many kinds
                raise SlgError(f'{name}: its {view} portrait cannot be read as a picture ({exc})') from exc
        for slot in VOICE_SLOTS:
            member = f'voice/{slot}.wav'
            if member in names:
                data = zf.read(member)
                if len(data) < 12 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
                    raise SlgError(f'{name}: {member} is not a WAV file')
                model.voice[slot] = data
                model.voice_slots[slot] = 'own'
        if blocks:
            with InputSources(input_dir) as sources:
                for i in indexes:
                    model.blocks[i] = apply_delta(zf.read(f'models/{i}.slgd'), sources,
                                                  f'{name}: its {"High" if i == HIGH_FILE else "Low"} model')
    return model


def is_slgmodel(path: str) -> bool:
    return str(path).lower().endswith(SUFFIX)
