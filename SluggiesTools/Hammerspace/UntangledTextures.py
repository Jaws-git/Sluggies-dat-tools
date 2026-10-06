"""Re-apply ``export.py --untangle`` texture untangling to an unused character's block.

The untangle export gives every duplicate texture a unique Dolphin hash by
flipping a few image bytes (``tpl.TEXDescriptor.ensureUniqueDolphinBasename``).
The unused characters (UntanglePolicy) come after their playable owners in
export order, so nearly all of their textures get flipped. Their copies then
differ from vanilla by 1-15 bytes, all inside texture payloads (measured on a
live output, 2026-09-28), and their `.sluggie` records the untangled names.

The flips follow a fixed sequence, so they can be replayed on vanilla bytes
until the Dolphin name matches the `.sluggie`'s ``TextureFileName``. That makes
an unused character's baseline a pure function of the input DAT plus its
`.sluggie`, even after the export's copy has been zeroed.

Only textures whose payload is still the vanilla one are replayed. An edited
or re-encoded texture has a new hash anyway, and replaying on it would never
match.
"""
from __future__ import annotations

import glob
import json
import os
import struct

try:
    from tpl import TEXDescriptor
except ImportError:  # pragma: no cover - SluggiesTools not on sys.path yet
    import sys
    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
    from tpl import TEXDescriptor

#: Matches the export's untangle ``max_attempts``.
MAX_ATTEMPTS = 8192
_BITS_PER_PIXEL = {0: 4, 1: 8, 2: 8, 3: 16, 4: 16, 5: 16, 6: 32, 8: 4, 9: 8, 10: 16, 14: 4}
_MODELS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '2_Output_Models'))


def _helper():
    if __package__:
        from . import HammerspaceHelper
    else:
        import HammerspaceHelper
    return HammerspaceHelper


class _Texture:
    """One texture's level-0 image range and palette inside a block."""

    def __init__(self, block, tex_start, descriptor):
        data_ptr, palette_ptr = struct.unpack_from('>II', block, descriptor)
        self.height, self.width = struct.unpack_from('>HH', block, descriptor + 8)
        self.format = block[descriptor + 0x17]
        palette_entries = struct.unpack_from('>H', block, descriptor + 0x18)[0]
        # Same payload the export hashes: level 0 only (TEXDescriptor._data_length).
        length = self.height * self.width * _BITS_PER_PIXEL[self.format] >> 3
        self.image_start = tex_start + data_ptr
        self.image_end = self.image_start + length
        self.tlut = b''
        if palette_ptr > 0 and palette_entries > 0:
            start = tex_start + palette_ptr
            self.tlut = bytes(block[start:start + palette_entries * 2])

    def name(self, image) -> str:
        stub = TEXDescriptor.__new__(TEXDescriptor)
        stub.width, stub.height, stub.format = self.width, self.height, self.format
        return stub.dolphinTextureBasenameForPayload(bytes(image), self.tlut)


def textures(block, model_start: int) -> list[_Texture]:
    """The TEX descriptors of the model at ``model_start``, in index order."""
    tex_ptr = struct.unpack_from('>I', block, model_start + 0x0C)[0]
    if not tex_ptr:
        return []
    tex_start = model_start + tex_ptr
    count = struct.unpack_from('>H', block, tex_start)[0]
    return [_Texture(block, tex_start, tex_start + 4 + index * 0x20) for index in range(count)]


def replay(image: bytes, texture: _Texture, target: str, max_attempts: int = MAX_ATTEMPTS):
    """Return ``image`` with the export's flip sequence applied until its
    Dolphin name is ``target``, or None when no attempt matches."""
    if texture.name(image) == target:
        return bytes(image)
    mutated = bytearray(image)
    length = len(mutated)
    if not length:
        return None
    for attempt in range(1, max_attempts + 1):
        pos = (attempt - 1) % length
        rounds = (attempt - 1) // length
        mask = ((rounds * 37) + (attempt * 17) + 1) & 0xFF or 1
        mutated[pos] ^= mask
        if texture.name(mutated) == target:
            return bytes(mutated)
    return None


def reapply(block: bytearray, model_start: int, texture_descriptors, vanilla_block,
            vanilla_model_start: int) -> list[str]:
    """Re-apply untangled texture bytes in place; return log notes.

    ``texture_descriptors`` is the `.sluggie`'s ``TextureDescriptors`` list.
    A texture is only replayed when its payload in ``block`` equals the
    vanilla payload at the same index.
    """
    notes = []
    current = textures(block, model_start)
    vanilla = textures(vanilla_block, vanilla_model_start)
    for entry in texture_descriptors or ():
        index = int(entry.get('TextureIndex', -1))
        target = (entry.get('TextureFileName') or '').removesuffix('.png')
        if not target or not 0 <= index < min(len(current), len(vanilla)):
            continue
        texture, source = current[index], vanilla[index]
        image = bytes(block[texture.image_start:texture.image_end])
        if image != bytes(vanilla_block[source.image_start:source.image_end]):
            continue  # edited or re-encoded: it has its own hash already
        replayed = replay(image, texture, target)
        if replayed is None:
            notes.append(f'tex {index}: no flip sequence reproduces {target}.png; kept vanilla bytes')
        elif replayed != image:
            block[texture.image_start:texture.image_end] = replayed
            notes.append(f'tex {index}: re-applied untangled name {target}.png')
    return notes


def model_start_in_entry(entry: bytes, model_length: int | None) -> int:
    """Offset of the `.sluggie`'s model inside its DOL entry (archive prefix)."""
    if model_length is None or model_length == len(entry):
        return 0
    try:
        from ArchiveContainer import parse_archive_container
    except ImportError:
        from .ArchiveContainer import parse_archive_container
    layout = parse_archive_container(entry)
    members = [m for m in (layout.members if layout else ()) if m.length == model_length]
    if len(members) != 1:
        raise ValueError(f'cannot place a {model_length:,}-byte model inside the DOL entry')
    return members[0].offset


def split_baseline(chunk_number: int, file_index: int, model: dict | None) -> tuple[bytes, list[str]]:
    """The baseline block of a split route: its vanilla entry with the
    untangled textures from ``model`` (a `.sluggie`'s ``SluggiesModel``)
    re-applied. Plain vanilla when ``model`` is None."""
    hh = _helper()
    offset, length = hh.readDolEntry(chunk_number, file_index)
    with open(hh.INPUT_DAT, 'rb') as source:
        source.seek(offset)
        vanilla = source.read(length)
    if model is None or not model.get('TextureDescriptors'):
        return vanilla, []
    start = model_start_in_entry(vanilla, model.get('ModelLength'))
    block = bytearray(vanilla)
    notes = reapply(block, start, model['TextureDescriptors'], vanilla, start)
    return bytes(block), notes


def split_at_baseline(chunk_number: int, file_index: int, models_dir: str = _MODELS_DIR) -> bool:
    """Whether a split route holds its baseline: its own copy (not the owner's route) whose bytes equal
    ``split_baseline`` with the exported ``.sluggie`` (without one: the vanilla bytes). False for a patched or
    re-tangled route."""
    import LodPartnerGuard
    hh = _helper()
    if hh.readOutputDolEntry(chunk_number, file_index) == hh.readDolEntry(chunk_number, file_index):
        return False
    block, _notes = split_baseline(chunk_number, file_index, find_sluggie_model(chunk_number, file_index, models_dir))
    return LodPartnerGuard.read_current_block(chunk_number, file_index) == block


def find_sluggie_model(chunk_number: int, file_index: int, models_dir: str = _MODELS_DIR) -> dict | None:
    """The exported ``SluggiesModel`` for a route, searched in its dir folder."""
    for path in sorted(glob.glob(os.path.join(glob.escape(models_dir), f'{chunk_number} *', '**', '*.sluggie'),
                                 recursive=True)):
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                model = json.load(handle)['SluggiesModel']
        except (OSError, ValueError, KeyError):
            continue
        if model.get('ChunkNumber') == chunk_number and model.get('FileIndex') == file_index:
            return model
    return None
