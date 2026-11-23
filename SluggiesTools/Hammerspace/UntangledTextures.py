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
import os

try:
    from tpl import TEXDescriptor
except ImportError:  # pragma: no cover - SluggiesTools not on sys.path yet
    import sys
    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
    from tpl import TEXDescriptor
import model_files
from binfmt import load_sluggie
try:
    from ModelBlock import ModelBlock, texture_payload_len
except ImportError:  # imported as Hammerspace.UntangledTextures
    from .ModelBlock import ModelBlock, texture_payload_len

#: The export's untangle ``max_attempts``.
MAX_ATTEMPTS = 8192
#: Attempts a replay tries. The k-th copy of one texture needs k flips; over
#: every export in 2_Output_Assets (1,237 models, 6,900 textures, 2026-11-17)
#: the most was 15. A replay that cannot match (a texture flipped again by
#: the old re-untangle bug) hashes the whole image per attempt, about 67 s
#: for a 512 x 512 CMPR texture at the export's limit; this cap makes it
#: about 2 s.
REPLAY_MAX_ATTEMPTS = 256
_MODELS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '2_Output_Assets'))


def _helper():
    if __package__:
        from . import HammerspaceHelper
    else:
        import HammerspaceHelper
    return HammerspaceHelper


def _dol_records():
    if __package__:
        from . import DolRecords
    else:
        import DolRecords
    return DolRecords


class _Texture:
    """One texture's level-0 image range and palette inside a block."""

    def __init__(self, tex, descriptor):
        data_ptr, palette_ptr = descriptor.image_ptr, descriptor.palette_ptr
        self.height, self.width = descriptor.height, descriptor.width
        self.format = descriptor.format
        palette_entries = descriptor.palette_entries
        # Same payload the export hashes: level 0 only (TEXDescriptor._data_length).
        length = texture_payload_len(self.format, self.width, self.height)
        if length is None:
            raise KeyError(self.format)
        self.image_start = tex.offset + data_ptr
        self.image_end = self.image_start + length
        self.tlut = b''
        if palette_ptr > 0 and palette_entries > 0:
            self.tlut = tex.model.raw(tex.offset + palette_ptr, palette_entries * 2)

    def name(self, image) -> str:
        stub = TEXDescriptor.__new__(TEXDescriptor)
        stub.width, stub.height, stub.format = self.width, self.height, self.format
        return stub.dolphinTextureBasenameForPayload(bytes(image), self.tlut)


def textures(block, model_start: int) -> list[_Texture]:
    """The TEX descriptors of the model at ``model_start``, in index order.
    Raises ``ModelBlockError`` (a ``struct.error``) for a block cut short and
    ``KeyError`` for a texture format the export does not know."""
    tex = ModelBlock(block, model_start).tex()
    if tex is None:
        return []
    return [_Texture(tex, descriptor) for descriptor in tex.descriptors()]


def replay(image: bytes, texture: _Texture, target: str, max_attempts: int = REPLAY_MAX_ATTEMPTS):
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
            notes.append(f'tex {index}: no flip sequence of up to {REPLAY_MAX_ATTEMPTS} attempts reproduces '
                         f'{target}.png; kept vanilla bytes')
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
    offset, length = _dol_records().readDolEntry(chunk_number, file_index)
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
    dol = _dol_records()
    if dol.readOutputDolEntry(chunk_number, file_index) == dol.readDolEntry(chunk_number, file_index):
        return False
    block, _notes = split_baseline(chunk_number, file_index, find_sluggie_model(chunk_number, file_index, models_dir))
    return LodPartnerGuard.read_current_block(chunk_number, file_index) == block


def find_sluggie_model(chunk_number: int, file_index: int, models_dir: str = _MODELS_DIR) -> dict | None:
    """The exported ``SluggiesModel`` for a route, searched in its dir folder (a slot export, ``DonorEntry``, is
    another block's export: skipped). Exported files (named after their folder) are tried before variants saved
    under new names."""
    paths = sorted(glob.glob(os.path.join(glob.escape(models_dir), f'{chunk_number} *', '**', '*.sluggie'),
                             recursive=True))
    for path in sorted(paths, key=lambda p: not model_files.is_exported_name(p)):
        try:
            model = load_sluggie(path)['SluggiesModel']
        except (OSError, ValueError, KeyError):
            continue
        if (model.get('ChunkNumber') == chunk_number and model.get('FileIndex') == file_index
                and model.get('DonorEntry') is None):
            return model
    return None
