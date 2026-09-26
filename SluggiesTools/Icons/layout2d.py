"""Read-only parser for the game's 2D layout banks.

A 2D layout bank is a ``dt_na.dat`` entry that holds a screen's textures and
its sprite layout. The player-icon bank (dir 0 entry 1574) and the bank that
holds the character-select grid (dir 0 entry 1591) both use this format.

Bank layout (all big-endian, all offsets bank-relative unless noted):

  +0x00  u32  0x20
  +0x04  u32  container offset
  +0x20  u16  texture count; texture descriptors follow at 0x24 + i * 0x20

Container (at the container offset):

  +0x00  u16  screen width (0x280), u16 screen height (0x1C0)
  +0x04  u16, u16  unknown (0x3C24, 0x0002 in every bank seen)
  +0x08  u32  descriptor offset, relative to the container (always 0x14)
  +0x0C  u32  resource section offset, relative to the container
  +0x10  u32  container endpoint, relative to the container; it equals the
              end of the resource section
  +0x14  descriptor: u32 element count N, then N + 1 signed pointers
         relative to the descriptor: the resource section, then elements
         0 .. N-1

Element (``elem +`` offsets):

  +0x00  u16  flags (unknown; 0 or 1), u16 zero
  +0x04  u32  sub-block count S
  +0x08  u32  total element length
  +0x0C  S x u32 sub-block offsets, relative to the element
  sub-block 0 is a 0x10-byte info block; sub-blocks 1 .. S-1 are tracks.

Track: u16 record count, u16 size of the first record in bytes, then the
records back to back. Records vary in size inside one track. Each starts with
u8 continuation flag (0 on the first record, 1 on the others), u8 record size
in 32-bit words (``0x02`` = 8, ``0x0F`` = 0x3C, ``0x14`` = 0x50,
``0x16`` = 0x58 bytes), u16 key. The key is the keyframe number for animated
sprites and the character ID in the icon source tables. Records of 0x3C bytes
and more continue with u16 kind and u16 resource id; 8-byte records carry no
sprite. 0x3C-byte sprite records also carry:

  +0x08  s16 x, s16 y (screen position; repeated at +0x18 and +0x1C)
  +0x2C  RGBA colour (alpha 0 hides the sprite)
  +0x34  f32 scale x, f32 scale y

Resource section: u32 row count, u32 section length, then 0x14-byte rows of
u16 texture page, u16 zero and four f32 UV bounds.

The character-select grid is element ``0xBA`` of entry 1591. It has 42
tracks, one per square: a 10 x 4 grid of visible squares, then two hidden
squares in the 11th and 12th column positions of the bottom row.
"""

import argparse
import os
import struct
from dataclasses import dataclass

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
import sys as _sys
if _TOOLS_DIR not in _sys.path:
    _sys.path.insert(0, _TOOLS_DIR)
import slogger as _slogger
_slogger.configure()

ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
INPUT_DAT = os.path.join(ROOT, '1_Input', 'dt_na.dat')
INPUT_DOL = os.path.join(ROOT, '1_Input', 'main.dol')

BANK_MAGIC = 0x20
DESCRIPTOR_OFFSET = 0x14
RESOURCE_ROW_SIZE = 0x14
SPRITE_RECORD_SIZE = 0x3C
FOLLOWING_RECORD_FLAG = 0x0100

# DOL directory record of the bank that holds the character-select grid
# (dir 0, file 1591). All 120 directory windows that list this file share
# this one record. The three language slots point at three separate copies
# of the same size; they differ in their text textures.
CSS_LAYOUT_DOL_RECORD = 0x68E1B8
CSS_LAYOUT_LENGTH = 0x138E50
CSS_LAYOUT_OFFSETS = {'en': 0x19FB8C40, 'sp': 0x1A0F1AA0, 'fr': 0x1A22A900}
CSS_GRID_ELEMENT = 0xBA
DAT_FNAME_PTR = 0x8067F658


class Layout2dError(RuntimeError):
    pass


@dataclass(frozen=True)
class Track:
    offset: int
    records: tuple[bytes, ...]


@dataclass(frozen=True)
class Element:
    index: int
    offset: int
    flags: int
    length: int
    info: bytes
    tracks: tuple[Track, ...]


@dataclass(frozen=True)
class Bank:
    container_offset: int
    width: int
    height: int
    texture_count: int
    resource_offset: int
    endpoint: int
    element_offsets: tuple[int, ...]


@dataclass(frozen=True)
class SpriteKey:
    flags: int
    key: int
    kind: int
    resource: int
    x: int
    y: int
    rgba: int
    scale: tuple[float, float]

    @property
    def visible(self) -> bool:
        return (self.rgba & 0xFF) != 0


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise Layout2dError(f'read at 0x{offset:X} is outside the bank')
    return struct.unpack_from('>I', data, offset)[0]


def parse_bank(data: bytes) -> Bank:
    if len(data) < 0x24 or _u32(data, 0) != BANK_MAGIC:
        raise Layout2dError('not a 2D layout bank (missing 0x20 header word)')
    container = _u32(data, 4)
    if container + DESCRIPTOR_OFFSET + 4 > len(data):
        raise Layout2dError(f'container offset 0x{container:X} is outside the bank')
    width, height = struct.unpack_from('>HH', data, container)
    descriptor_rel, resource_rel, endpoint_rel = struct.unpack_from('>III', data, container + 8)
    if descriptor_rel != DESCRIPTOR_OFFSET:
        raise Layout2dError(f'unexpected descriptor offset 0x{descriptor_rel:X}')
    descriptor = container + DESCRIPTOR_OFFSET
    count = _u32(data, descriptor)
    if descriptor + 4 + 4 * (count + 1) > len(data):
        raise Layout2dError(f'element table ({count} elements) is outside the bank')
    pointers = struct.unpack_from(f'>{count + 1}i', data, descriptor + 4)
    resource = descriptor + pointers[0]
    if resource != container + resource_rel:
        raise Layout2dError('resource pointer does not match the container header')
    return Bank(
        container_offset=container,
        width=width,
        height=height,
        texture_count=struct.unpack_from('>H', data, 0x20)[0],
        resource_offset=resource,
        endpoint=container + endpoint_rel,
        element_offsets=tuple(descriptor + pointer for pointer in pointers[1:]),
    )


def _parse_track(data: bytes, element_index: int, start: int, end: int) -> Track:
    count, first_size = struct.unpack_from('>HH', data, start)
    records = []
    cursor = start + 4
    for number in range(count):
        if cursor + 2 > end:
            raise Layout2dError(f'element 0x{element_index:X} track at 0x{start:X} is truncated')
        follows, words = data[cursor], data[cursor + 1]
        size = words * 4
        if follows != (1 if number else 0) or size < 8 or cursor + size > end:
            raise Layout2dError(
                f'element 0x{element_index:X} track at 0x{start:X}: bad record {number} header '
                f'{data[cursor:cursor + 2].hex()}'
            )
        records.append(data[cursor:cursor + size])
        cursor += size
    if cursor != end or (records and len(records[0]) != first_size):
        raise Layout2dError(
            f'element 0x{element_index:X} track at 0x{start:X} does not match its header '
            f'({count} records, first 0x{first_size:X} bytes, span 0x{end - start:X})'
        )
    return Track(start, tuple(records))


def parse_element(data: bytes, bank: Bank, index: int) -> Element:
    if not 0 <= index < len(bank.element_offsets):
        raise Layout2dError(f'element 0x{index:X} does not exist ({len(bank.element_offsets)} elements)')
    start = bank.element_offsets[index]
    flags = struct.unpack_from('>H', data, start)[0]
    sub_count, length = struct.unpack_from('>II', data, start + 4)
    if start + length > len(data) or 0x0C + 4 * sub_count > length:
        raise Layout2dError(f'element 0x{index:X} is truncated')
    subs = struct.unpack_from(f'>{sub_count}I', data, start + 0x0C)
    ends = subs[1:] + (length,)
    tracks = []
    for sub_start, sub_end in zip(subs[1:], ends[1:]):
        tracks.append(_parse_track(data, index, start + sub_start, start + sub_end))
    info = data[start + subs[0]:start + ends[0]] if sub_count else b''
    return Element(index, start, flags, length, info, tuple(tracks))


def parse_sprite_key(record: bytes) -> SpriteKey:
    if len(record) != SPRITE_RECORD_SIZE:
        raise Layout2dError(f'sprite record must be 0x{SPRITE_RECORD_SIZE:X} bytes, not 0x{len(record):X}')
    flags, key, kind, resource, x, y = struct.unpack_from('>HHHHhh', record, 0)
    rgba = struct.unpack_from('>I', record, 0x2C)[0]
    scale = struct.unpack_from('>ff', record, 0x34)
    return SpriteKey(flags, key, kind, resource, x, y, rgba, scale)


def resource_rows(data: bytes, bank: Bank) -> list[tuple[int, tuple[float, float, float, float]]]:
    count, length = struct.unpack_from('>II', data, bank.resource_offset)
    if 8 + count * RESOURCE_ROW_SIZE > length or bank.resource_offset + length != bank.endpoint:
        raise Layout2dError('resource section does not match the container endpoint')
    rows = []
    for index in range(count):
        row = bank.resource_offset + 8 + index * RESOURCE_ROW_SIZE
        rows.append((struct.unpack_from('>H', data, row)[0], struct.unpack_from('>4f', data, row + 4)))
    return rows


def grid_squares(data: bytes, element_index: int = CSS_GRID_ELEMENT) -> list[SpriteKey]:
    """Return the first keyframe of every square track in a grid element."""
    bank = parse_bank(data)
    element = parse_element(data, bank, element_index)
    squares = []
    for track in element.tracks:
        if not track.records or len(track.records[0]) != SPRITE_RECORD_SIZE:
            raise Layout2dError(f'element 0x{element_index:X} has a non-sprite track')
        squares.append(parse_sprite_key(track.records[0]))
    return squares


def read_css_layout_route(dol_path: str = INPUT_DOL) -> dict[str, tuple[int, int]]:
    """Return ``{lang: (offset, length)}`` from the CSS layout's DOL record."""
    with open(dol_path, 'rb') as dol:
        dol.seek(CSS_LAYOUT_DOL_RECORD)
        words = struct.unpack('>12I', dol.read(48))
    route = {}
    for slot, lang in enumerate(('en', 'sp', 'fr')):
        name, length, offset, _alloc = words[slot * 4:slot * 4 + 4]
        if name != DAT_FNAME_PTR:
            raise Layout2dError(f'DOL record 0x{CSS_LAYOUT_DOL_RECORD:X} is not a dt_na.dat entry')
        route[lang] = (offset, length)
    return route


def main() -> int:
    parser = argparse.ArgumentParser(description='Dump a 2D layout grid element from dt_na.dat.')
    parser.add_argument('--lang', choices=('en', 'sp', 'fr'), default='en')
    parser.add_argument('--element', type=lambda value: int(value, 0), default=CSS_GRID_ELEMENT)
    args = parser.parse_args()

    offset, length = read_css_layout_route()[args.lang]
    with open(INPUT_DAT, 'rb') as dat:
        dat.seek(offset)
        data = dat.read(length)
    bank = parse_bank(data)
    squares = grid_squares(data, args.element)
    _slogger.info(
        f'CSS layout ({args.lang}) at 0x{offset:08X}+0x{length:X}: {len(bank.element_offsets)} elements, '
        f'{bank.texture_count} textures; element 0x{args.element:X} has {len(squares)} squares',
        source='icons.layout2d',
    )
    for number, square in enumerate(squares, 1):
        _slogger.info(
            f'  square {number:2d}: x={square.x:4d} y={square.y:4d} resource=0x{square.resource:X} '
            f'{"visible" if square.visible else "hidden"}',
            source='icons.layout2d',
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
