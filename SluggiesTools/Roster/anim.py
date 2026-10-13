"""Animation banks of a character slot: files 6-14 of its model directory.

Each character directory holds nine ANM banks (files 6-14). A bank is a list of
sequences (animations); each sequence a list of tracks; a track names the ANM
track id it drives. A model's ACT section maps its bones to track ids (user
data kind 3, ``_docs/_docs_model_format/act_section.html#user-data``), so a
bank fits every skeleton that has bones for the track ids it animates.

Survey of the stock banks (``1_Input/dt_na.dat``, 2026-10-12):

* the sequence count tells the file: 44, 28, 17, 12, 4, 9, 24 (22 on the Miis),
  2 and 5 sequences for files 6-14, on every character (``SEQUENCES``);
* track ids are shared names across species (0, 2, 3, ... on every body type),
  so another character's bank can fit a skeleton; ids 0x8000+ drive no bone;
* every stock bank animates a few ids its own skeleton lacks (0x3C and
  0x8x08/0xCx08 on most characters): the game ignores them, so the check
  allows whatever the slot's stock bank animates too (``problems``);
* recolours carry byte-identical copies of their family's banks; the unused
  characters (dirs 89-94) and Dark Bones share their base's routes.

The pure part: the bank reader, the skeleton's track ids, the fit check. No
game files are read here.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

FILES = tuple(range(6, 15))
MAGICS = (0x01321AFD, 0x013240DB, 0x01324210)      # version dates 2006-09-25, 2007-06-19, 2007-09-28
SEQUENCES = {6: (44,), 7: (28,), 8: (17,), 9: (12,), 10: (4,), 11: (9,), 12: (24, 22), 13: (2,), 14: (5,)}
HEADER_SIZE, SEQUENCE_SIZE, TRACK_SIZE = 0x18, 12, 16
CHANNEL_MASK = 0x1F                     # type bits 0-4: translation, scale, Euler, quaternion, matrix
NO_TRACK = 0xFFFF
TRACK_KIND = 3                          # ACT user data: the bone-to-track table
MODEL_ACT = 0x08                        # model block header: the ACT section's offset
ACT_BONE_COUNT, ACT_USER_SIZE, ACT_USER_PTR = 0x06, 0x18, 0x1C
DESCRIPTOR_SIZE = 0x0C
SHOWN_IDS = 6                           # track ids listed in a message


class AnimError(ValueError):
    pass


@dataclass(frozen=True)
class Bank:
    """An ANM bank as the checks see it: ``sequences`` holds each sequence's animated track ids (tracks with
    channels; channel-less tracks carry opaque words and drive nothing)."""
    magic: int
    sequences: tuple
    length: int

    @property
    def count(self) -> int:
        return len(self.sequences)

    @property
    def tracks(self) -> frozenset:
        return frozenset().union(*self.sequences)


def label(file: int) -> str:
    return f'Animation bank {file}'


def read_bank(data: bytes) -> Bank:
    """The bank in ``data``; anything that is not a readable ANM bank raises ``AnimError``."""
    if len(data) < HEADER_SIZE:
        raise AnimError(f'{len(data)} bytes: too short for an animation bank')
    magic, seq_ptr, _bank_id, seq_count = struct.unpack_from('>IIHH', data, 0)
    if magic not in MAGICS:
        raise AnimError(f'not an animation bank (magic 0x{magic:08X})')
    if seq_count == 0 or seq_ptr + SEQUENCE_SIZE * seq_count > len(data):
        raise AnimError(f'the sequence table ({seq_count} sequences) runs past the end of the file')
    sequences = []
    for s in range(seq_count):
        _name, track_ptr, track_count, _pad = struct.unpack_from('>IIHH', data, seq_ptr + SEQUENCE_SIZE * s)
        if track_count and track_ptr + TRACK_SIZE * track_count > len(data):
            raise AnimError(f'sequence {s}: its track table runs past the end of the file')
        ids = set()
        for t in range(track_count):
            track_id, = struct.unpack_from('>H', data, track_ptr + TRACK_SIZE * t + 10)
            track_type = data[track_ptr + TRACK_SIZE * t + 13]
            if track_type & CHANNEL_MASK:
                ids.add(track_id)
        sequences.append(frozenset(ids))
    return Bank(magic, tuple(sequences), len(data))


def bank_file(bank: Bank) -> int:
    """The file (6-14) a bank with this many sequences belongs to; ``AnimError`` for a count no stock bank has."""
    for file, counts in SEQUENCES.items():
        if bank.count in counts:
            return file
    raise AnimError(f'it holds {bank.count} animations, which matches none of the nine animation banks '
                    '(44, 28, 17, 12, 4, 9, 24, 2 and 5 animations)')


def skeleton_tracks(block: bytes | None) -> frozenset | None:
    """The track ids the bones of a model block read (its ACT kind-3 table, 0xFFFF left out); None when the block
    has no readable ACT section or no table."""
    if not block or len(block) < MODEL_ACT + 4:
        return None
    try:
        act, = struct.unpack_from('>I', block, MODEL_ACT)
        bone_count, = struct.unpack_from('>H', block, act + ACT_BONE_COUNT)
        user_size, user_ptr = struct.unpack_from('>II', block, act + ACT_USER_SIZE)
        if not act or not user_size or not user_ptr:
            return None
        pos, end = act + user_ptr, act + user_ptr + user_size
        while pos < end:
            size, kind, _count, data_ptr = struct.unpack_from('>IHHI', block, pos)
            if size < DESCRIPTOR_SIZE:
                return None
            if kind == TRACK_KIND:
                ids = struct.unpack_from(f'>{bone_count}H', block, pos + data_ptr)
                return frozenset(i for i in ids if i != NO_TRACK)
            pos += size
    except struct.error:
        return None
    return None


def _ids(values) -> str:
    shown = ', '.join(f'0x{v:X}' for v in sorted(values)[:SHOWN_IDS])
    return shown + (', ...' if len(values) > SHOWN_IDS else '')


def problems(bank: Bank, file: int, stock: Bank, skeleton: frozenset) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for loading ``bank`` as file ``file`` of a slot whose stock bank there is ``stock``
    and whose bones read the track ids ``skeleton``.

    Errors: another sequence count than the stock bank (the game picks animations by number); animated track ids
    that neither the skeleton has nor the stock bank animates (it was made for another skeleton). Warning: bones the
    stock bank animates that get no track (they stay in their rest pose)."""
    errors, warnings = [], []
    if bank.count != stock.count:
        errors.append(f'it holds {bank.count} animations, the slot\'s animation bank {file} {stock.count}')
    foreign = bank.tracks - skeleton - stock.tracks
    if foreign:
        errors.append(f'it animates {len(foreign)} track id{"s" if len(foreign) != 1 else ""} the slot\'s skeleton '
                      f'has no bone for ({_ids(foreign)}): it was made for another skeleton, or it animates an '
                      'added bone whose model is not patched in yet (patch the .sluggie first, then the animation)')
    missing = (stock.tracks & skeleton) - bank.tracks
    if missing and not errors:
        warnings.append(f'{len(missing)} bone{"s" if len(missing) != 1 else ""} the stock bank animates '
                        f'{"have" if len(missing) != 1 else "has"} no track in it ({_ids(missing)}): '
                        f'{"they stay" if len(missing) != 1 else "it stays"} in the rest pose')
    return errors, warnings
