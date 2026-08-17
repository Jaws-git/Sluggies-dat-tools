from __future__ import annotations

import ntpath
import os
import struct
import subprocess
import tempfile
import slogger
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

TPL_MAGIC = 0x0020AF30

_GX_FORMATS = {
    0x0: "I4",
    0x1: "I8",
    0x2: "IA4",
    0x3: "IA8",
    0x4: "RGB565",
    0x5: "RGB5A3",
    0x6: "RGBA8",
    0x8: "C4",
    0x9: "C8",
    0xA: "C14X2",
    0xE: "CMPR",
}

# Explicit WIMGT target for each supported GX image format. Indexed formats
# (C4/C8/C14X2) require a donor palette token, which wimgt_target_for() appends
# based on the descriptor's palette format. Direct-color formats never use a
# palette token. WIMGT must never be allowed to choose a format automatically.
WIMGT_IMAGE_TARGETS = {
    0x0: "TPL.I4",
    0x1: "TPL.I8",
    0x2: "TPL.IA4",
    0x3: "TPL.IA8",
    0x4: "TPL.RGB565",
    0x5: "TPL.RGB5A3",
    0x6: "TPL.RGBA8",
    0x8: "TPL.C4",
    0x9: "TPL.C8",
    0xA: "TPL.C14X2",
    0xE: "TPL.CMPR",
}

# WIMGT palette token for each supported donor palette format.
WIMGT_PALETTE_TOKENS = {
    0x0: "P-IA8",
    0x1: "P-RGB565",
    0x2: "P-RGB5A3",
}

# GX image formats that require a palette.
_INDEXED_FORMATS = frozenset((0x8, 0x9, 0xA))


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor if value > 0 else 0


def _image_payload_size(width: int, height: int, gx_format: int) -> int:
    if gx_format not in _GX_FORMATS:
        raise ValueError(f"unsupported GX image format 0x{gx_format:08X}")

    if gx_format in (0x0, 0x8):
        return _ceil_div(width, 8) * _ceil_div(height, 8) * 32
    if gx_format in (0x1, 0x2, 0x9):
        return _ceil_div(width, 8) * _ceil_div(height, 4) * 32
    if gx_format in (0x3, 0x4, 0x5, 0xA):
        return _ceil_div(width, 4) * _ceil_div(height, 4) * 32
    if gx_format == 0x6:
        return _ceil_div(width, 4) * _ceil_div(height, 4) * 64
    if gx_format == 0xE:
        return _ceil_div(width, 4) * _ceil_div(height, 4) * 8
    raise ValueError(f"unsupported GX image format 0x{gx_format:08X}")


def _mip_chain_payload_length(
    gx_format: int,
    width: int,
    height: int,
    additional_mip_count: int,
) -> int:
    """Return the exact stored byte length of a full mip chain.

    Sums the encoded byte length of each level from zero through
    ``additional_mip_count`` using the proven donor level-dimension contract
    (milestone 0.1). Used to report the expected payload length for a skipped
    mipmapped texture (PLAN 3.1, fifth bullet).
    """
    total = 0
    for level in range(additional_mip_count + 1):
        w = max(1, width // (1 << level))
        h = max(1, height // (1 << level))
        total += _image_payload_size(w, h, gx_format)
    return total


@dataclass(frozen=True)
class ParsedSingleImageTpl:
    magic: int
    image_count: int
    table_offset: int
    image_offset: int
    palette_offset: int
    width: int
    height: int
    format: int
    format_name: str
    image_payload_offset: int
    palette_payload_offset: int
    image_data: bytes
    palette_data: bytes
    palette_entries: int
    palette_format: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "magic": self.magic,
            "image_count": self.image_count,
            "table_offset": self.table_offset,
            "image_offset": self.image_offset,
            "palette_offset": self.palette_offset,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "format_name": self.format_name,
            "image_payload_offset": self.image_payload_offset,
            "palette_payload_offset": self.palette_payload_offset,
            "image_data": self.image_data,
            "palette_data": self.palette_data,
            "palette_entries": self.palette_entries,
            "palette_format": self.palette_format,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __getattr__(self, name: str) -> Any:
        if name in self.as_dict():
            return self.as_dict()[name]
        raise AttributeError(name)

    def __iter__(self):
        return iter(self.as_dict().items())


def parse_single_image_tpl(tpl_bytes: bytes | bytearray | memoryview) -> ParsedSingleImageTpl:
    data = bytes(tpl_bytes)
    if len(data) < 12:
        raise ValueError("TPL file is too short to contain a valid header")

    magic, image_count, table_offset = struct.unpack_from(">III", data, 0)
    if magic != TPL_MAGIC:
        raise ValueError(f"unexpected TPL magic: 0x{magic:08X}")
    if image_count != 1:
        raise ValueError(f"expected a single-image TPL, found {image_count} images")
    if table_offset < 12 or table_offset + 8 > len(data):
        raise ValueError("TPL image table is outside the file bounds")

    image_offset, palette_offset = struct.unpack_from(">II", data, table_offset)
    table_end = table_offset + 8
    if image_offset < table_end or image_offset + 12 > len(data):
        raise ValueError("TPL image descriptor is missing or outside the file bounds")
    if palette_offset and (palette_offset < table_end or palette_offset + 12 > len(data)):
        raise ValueError("TPL palette descriptor is outside the file bounds")

    height, width, gx_format, image_payload_offset = struct.unpack_from(">HHII", data, image_offset)
    if width == 0 or height == 0:
        raise ValueError(f"invalid image dimensions {width}x{height}")
    if gx_format not in _GX_FORMATS:
        raise ValueError(f"unsupported GX format code 0x{gx_format:08X}")

    expected_image_length = _image_payload_size(width, height, gx_format)
    image_end = image_payload_offset + expected_image_length
    if image_payload_offset < image_offset + 12:
        raise ValueError("image payload overlaps the descriptor table")
    if image_end > len(data):
        raise ValueError(
            f"image payload exceeds the file bounds: expected {expected_image_length} bytes, file length is {len(data)}"
        )

    palette_entries = 0
    palette_format = None
    palette_payload_offset = 0
    palette_data = b""
    if palette_offset != 0:
        palette_entries, _, palette_format, palette_payload_offset = struct.unpack_from(">HHII", data, palette_offset)
        if palette_entries <= 0:
            raise ValueError("palette descriptor has zero entries")
        if palette_format not in WIMGT_PALETTE_TOKENS:
            raise ValueError(f"unsupported TPL palette format code 0x{palette_format:02X}")

        palette_length = palette_entries * 2
        palette_end = palette_payload_offset + palette_length
        if palette_payload_offset < palette_offset + 12:
            raise ValueError("palette payload overlaps the palette descriptor")
        if palette_payload_offset + palette_length > len(data):
            raise ValueError("palette payload exceeds the file bounds")
        if max(image_payload_offset, palette_payload_offset) < min(image_end, palette_end):
            raise ValueError("image and palette payload ranges overlap")
        palette_data = data[palette_payload_offset:palette_end]

    if palette_offset and gx_format not in _INDEXED_FORMATS:
        raise ValueError(
            f"direct-color GX format 0x{gx_format:02X} has an unexpected palette descriptor"
        )
    if palette_offset == 0 and gx_format in (0x8, 0x9, 0xA):
        raise ValueError(f"indexed GX format 0x{gx_format:08X} is missing a palette descriptor")

    return ParsedSingleImageTpl(
        magic=magic,
        image_count=image_count,
        table_offset=table_offset,
        image_offset=image_offset,
        palette_offset=palette_offset,
        width=width,
        height=height,
        format=gx_format,
        format_name=_GX_FORMATS[gx_format],
        image_payload_offset=image_payload_offset,
        palette_payload_offset=palette_payload_offset,
        image_data=data[image_payload_offset:image_end],
        palette_data=palette_data,
        palette_entries=palette_entries,
        palette_format=palette_format,
    )


def parse_single_image_tpl_file(path: str | os.PathLike[str]) -> ParsedSingleImageTpl:
    with open(path, "rb") as tpl_file:
        return parse_single_image_tpl(tpl_file.read())


def parse_tpl(tpl_bytes: bytes | bytearray | memoryview) -> ParsedSingleImageTpl:
    return parse_single_image_tpl(tpl_bytes)


class TextureEncodingError(RuntimeError):
    """Raised when WIMGT encoding or TPL parsing fails.

    Carries the WIMGT target, captured stdout/stderr, and exit code so callers
    can surface actionable diagnostics.
    """

    def __init__(self, message, target=None, stdout=None, stderr=None, exit_code=None):
        super().__init__(message)
        self.target = target
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.exit_code = exit_code


def wimgt_target_for(gx_format: int, palette_format: int | None = None) -> str:
    """Return the explicit WIMGT target for a GX image format.

    Indexed formats (C4/C8/C14X2) require a donor palette format, which is
    appended as a palette token. Direct-color formats never use a palette token.
    Unsupported formats, or an indexed format without a supported palette, raise
    ValueError so WIMGT is never allowed to choose a format automatically.
    """
    base = WIMGT_IMAGE_TARGETS.get(gx_format)
    if base is None:
        raise ValueError(
            f"unsupported GX image format 0x{gx_format:02X}: no explicit WIMGT target"
        )

    if gx_format in _INDEXED_FORMATS:
        token = WIMGT_PALETTE_TOKENS.get(palette_format)
        if token is None:
            raise ValueError(
                f"indexed GX format 0x{gx_format:02X} requires a supported donor "
                f"palette format, got {palette_format!r}"
            )
        return f"{base}.{token}"

    return base


def resolve_tex_dir(sluggie_path: str | os.PathLike[str]) -> str:
    """Return the absolute path of the ``tex/`` folder for a ``.sluggie`` file.

    ``export.py`` writes each model's ``.sluggie`` JSON and its ``tex/`` folder
    into the same model directory, so ``tex/`` is a *sibling* of the ``.sluggie``
    file. Resolving it against the ``.sluggie``'s directory — rather than the
    process working directory — keeps texture re-import correct no matter where
    ``patch_inplace.py`` is launched from (PLAN 3.1, first bullet).
    """
    sluggie_dir = os.path.dirname(os.path.abspath(os.fspath(sluggie_path)))
    return os.path.join(sluggie_dir, "tex")


def resolve_texture_path(sluggie_path: str | os.PathLike[str], texture_file_name: str) -> str:
    """Return the absolute path of one texture PNG inside the ``.sluggie``'s ``tex/`` folder.

    ``texture_file_name`` is the descriptor's ``TextureFileName`` (a bare file
    name such as ``0.png``). Directory-component validation of that name is a
    separate concern (PLAN 3.1, second bullet); this helper only performs the
    sibling-directory resolution.
    """
    return os.path.join(resolve_tex_dir(sluggie_path), texture_file_name)


def validate_texture_file_name(texture_file_name: Any) -> str:
    """Validate one descriptor's ``TextureFileName`` and return it unchanged.

    The name must be a non-empty string that is a *bare* file name: no
    directory components (``/``, ``\\``, or ``.``/``..`` segments) and no
    Windows reserved characters. This keeps the resolved path inside the
    ``.sluggie``'s ``tex/`` folder (PLAN 3.1, second bullet).

    Raises ValueError with a message naming the offending value.
    """
    if not isinstance(texture_file_name, str):
        raise ValueError(
            f"TextureFileName must be a string, got {type(texture_file_name).__name__}"
        )
    if not texture_file_name:
        raise ValueError("TextureFileName must not be empty")

    # Reject any path separator (POSIX or Windows) anywhere in the name.
    if "/" in texture_file_name or "\\" in texture_file_name:
        raise ValueError(
            f"TextureFileName {texture_file_name!r} contains a directory component"
        )

    # Reject bare dot segments, which are directory references.
    if texture_file_name in (".", ".."):
        raise ValueError(
            f"TextureFileName {texture_file_name!r} contains a directory component"
        )

    # Reject Windows reserved characters and device names.
    if any(ch in texture_file_name for ch in '<>:"|?*'):
        raise ValueError(
            f"TextureFileName {texture_file_name!r} contains a reserved character"
        )
    stem = texture_file_name.rsplit(".", 1)[0].upper()
    if stem in ("CON", "PRN", "AUX", "NUL") or any(
        stem == f"COM{i}" or stem == f"LPT{i}" for i in range(1, 10)
    ):
        raise ValueError(
            f"TextureFileName {texture_file_name!r} is a reserved Windows device name"
        )

    return texture_file_name


def validate_texture_descriptors(descriptors: Sequence[Mapping[str, Any]]) -> list[str]:
    """Validate ``TextureFileName`` for every descriptor and return the names.

    Each descriptor must carry exactly one ``TextureFileName`` (a bare file
    name). Missing, null, or unsafe names are rejected with a message that
    includes the descriptor's ``TextureIndex`` so the offending texture can be
    identified (PLAN 3.1, second bullet).

    Returns the list of validated names in descriptor order.
    """
    names: list[str] = []
    for descriptor in descriptors:
        index = descriptor.get("TextureIndex", "?")
        raw_name = descriptor.get("TextureFileName")
        if raw_name is None:
            raise ValueError(
                f"texture {index} is missing a TextureFileName; "
                "create a new export to enable texture re-import"
            )
        try:
            names.append(validate_texture_file_name(raw_name))
        except ValueError as exc:
            raise ValueError(f"texture {index}: {exc}") from exc
    return names


def read_png_dimensions(png_path: str | os.PathLike[str]) -> tuple[int, int]:
    """Return the ``(width, height)`` of a PNG file using Pillow.

    This is the cheap, WIMGT-free dimension read used to validate a texture
    PNG against its descriptor before any encoding work (PLAN 3.1, fourth
    bullet).

    Raises TextureEncodingError if the file is missing, unreadable, or not a
    valid image.
    """
    from PIL import Image

    try:
        with Image.open(png_path) as img:
            width, height = img.size
    except FileNotFoundError as exc:
        raise TextureEncodingError(f"PNG not found: {png_path}") from exc
    except (OSError, ValueError) as exc:
        raise TextureEncodingError(
            f"could not read PNG dimensions from {png_path}: {exc}"
        ) from exc
    return width, height


def check_png_dimensions(
    png_path: str | os.PathLike[str],
    expected_width: int,
    expected_height: int,
) -> tuple[int, int]:
    """Validate that a PNG's dimensions match the descriptor's.

    Reads the PNG dimensions with Pillow (no WIMGT involved) and raises
    ValueError if the width or height differs from the descriptor. This is the
    "check PNG dimensions before invoking WIMGT" step (PLAN 3.1, fourth
    bullet): a dimension mismatch is rejected before any encoding work.

    Returns the actual ``(width, height)`` on success.
    """
    width, height = read_png_dimensions(png_path)
    if width != expected_width or height != expected_height:
        raise ValueError(
            f"PNG dimensions {width}x{height} do not match descriptor "
            f"{expected_width}x{expected_height} for {png_path}"
        )
    return width, height


def _validate_parsed_tpl_against_descriptor(
    descriptor: Mapping[str, Any],
    parsed: ParsedSingleImageTpl,
) -> None:
    """Validate an encoded TPL against its descriptor's authoritative metadata.

    The encoded TPL's metadata and payload sizes are the authoritative checks
    (PLAN "Required behavior"): the image format must equal the descriptor's
    ``Format``, and palette presence/format must match the descriptor. A
    mismatch means WIMGT produced something the donor cannot accept in place.

    Raises ValueError naming the offending field.
    """
    index = descriptor.get("TextureIndex", "?")

    expected_format = descriptor.get("Format")
    if expected_format is not None and parsed.format != expected_format:
        raise ValueError(
            f"texture {index}: encoded image format {parsed.format_name} "
            f"(0x{parsed.format:02X}) differs from descriptor Format "
            f"0x{expected_format:02X}"
        )

    has_palette = parsed.palette_offset != 0
    descriptor_has_palette = bool(descriptor.get("PaletteEntries"))
    if has_palette != descriptor_has_palette:
        raise ValueError(
            f"texture {index}: palette presence differs from descriptor "
            f"(encoded has palette={has_palette}, descriptor has palette={descriptor_has_palette})"
        )

    if has_palette:
        expected_palette_format = descriptor.get("PaletteFormat")
        if expected_palette_format is not None and parsed.palette_format != expected_palette_format:
            raise ValueError(
                f"texture {index}: encoded palette format 0x{parsed.palette_format:02X} "
                f"differs from descriptor PaletteFormat 0x{expected_palette_format:02X}"
            )


@dataclass(frozen=True)
class TexturePlanEntry:
    """One validated, encoded texture ready for in-place patching.

    Carries the descriptor's identity plus the exact image and optional
    palette payload bytes produced by WIMGT and validated against the
    descriptor. The patcher writes ``image_data`` (and ``palette_data`` when
    present) into the donor's proven payload ranges.
    """

    texture_index: int
    texture_file_name: str
    width: int
    height: int
    format: int
    format_name: str
    image_data: bytes
    palette_data: bytes
    palette_entries: int
    palette_format: int | None


@dataclass(frozen=True)
class SkippedTexture:
    """A texture skipped because its mip layout is unsupported.

    The donor's image and palette bytes are left unchanged. The plan carries
    the skip so the patcher can report it in the final summary (PLAN 3.1,
    fifth bullet; PLAN 4.2). This is the only nonfatal validation outcome;
    every other failure remains fatal.
    """

    texture_index: int
    texture_file_name: str
    expected_payload_length: int
    reason: str


@dataclass(frozen=True)
class TexturePlan:
    """The complete texture patch plan.

    ``entries`` holds the validated, encoded textures in descriptor order.
    ``skipped`` holds the mipmapped textures that were validated as an
    unsupported mip layout and left unchanged (PLAN 3.1, fifth bullet). The
    plan is only returned when every non-skipped descriptor encoded and
    validated successfully; any other failure aborts before a plan is produced.
    """

    entries: tuple[TexturePlanEntry, ...]
    skipped: tuple[SkippedTexture, ...] = ()

    def __iter__(self):
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, index: int) -> TexturePlanEntry:
        return self.entries[index]


@dataclass(frozen=True)
class TextureWrite:
    """One planned in-place DAT write for a texture payload (PLAN 3.2, first bullet).

    A write is represented as ``(kind, texture_index, offset, payload_length,
    bytes)``:

    - ``kind`` is ``"image"`` or ``"palette"``.
    - ``texture_index`` is the descriptor's ``TextureIndex`` that owns the write.
    - ``offset`` is the absolute file offset in ``dt_na.dat`` where the payload
      begins (the descriptor's ``ImageDataOffset`` or ``PaletteDataOffset``).
    - ``payload_length`` is the exact byte length of the payload being written.
    - ``bytes`` is the payload data; ``len(bytes)`` must equal
      ``payload_length``.

    The self-consistency invariant ``len(bytes) == payload_length`` is enforced
    in ``__post_init__`` so a malformed write cannot be constructed. Requiring
    that this length equal the *proven donor* length — and tail-filling a
    shorter generated palette to it — is the second bullet's validation. The
    dataclass unpacks as the five-field tuple named in the plan.
    """

    kind: str
    texture_index: int
    offset: int
    payload_length: int
    bytes: bytes

    def __post_init__(self):
        if self.offset < 0:
            raise ValueError(
                f"texture {self.texture_index} {self.kind} write has a negative offset: {self.offset}"
            )
        if self.payload_length < 0:
            raise ValueError(
                f"texture {self.texture_index} {self.kind} write has a negative payload_length: {self.payload_length}"
            )
        if len(self.bytes) != self.payload_length:
            raise ValueError(
                f"texture {self.texture_index} {self.kind} write is malformed: "
                f"payload_length is {self.payload_length} but {len(self.bytes)} bytes were provided"
            )

    def __iter__(self):
        yield self.kind
        yield self.texture_index
        yield self.offset
        yield self.payload_length
        yield self.bytes


def build_texture_plan(
    sluggie_path: str | os.PathLike[str],
    descriptors: Sequence[Mapping[str, Any]],
    wimgt_executable: str = "wimgt",
    encoder: Callable[..., ParsedSingleImageTpl] | None = None,
    warn: Callable[[str], None] | None = None,
) -> TexturePlan:
    """Encode and validate every descriptor, returning a plan only if all succeed.

    This is the all-or-nothing gate (PLAN 3.1, fifth bullet): for each
    descriptor it resolves the PNG from the ``.sluggie``'s ``tex/`` folder,
    checks the PNG dimensions against the descriptor, encodes with WIMGT, and
    validates the encoded TPL against the descriptor's authoritative metadata.

    If any non-skipped descriptor fails — missing file, bad dimensions,
    conversion error, or metadata mismatch — the function raises and returns
    *no* plan, so the patcher can abort before writing any edits. Only when
    every non-skipped descriptor succeeds is a :class:`TexturePlan` returned.

    A descriptor with ``AdditionalMipCount > 0`` is a mipmapped texture. The
    current single-image encoding path cannot validate a mip chain, so such a
    texture is treated as an unsupported mip layout: it is recorded in
    ``plan.skipped``, a warning is logged, and its donor bytes are left
    unchanged. This is the only nonfatal outcome (PLAN 3.1, fifth bullet).
    Missing files and bad dimensions remain fatal even for mipmapped
    textures: the skip exception covers only a *validated* unsupported mip
    layout, not malformed input (PLAN "Required behavior").

    ``encoder`` is injectable for testing; it defaults to
    :func:`encode_png_to_tpl` and must accept the same keyword arguments.
    ``warn`` is injectable for testing; it defaults to
    :func:`slogger.warning` and receives a single formatted message.
    """
    if encoder is None:
        encoder = encode_png_to_tpl
    if warn is None:
        warn = lambda message: slogger.warning(message, source="texture_helper")

    names = validate_texture_descriptors(descriptors)

    entries: list[TexturePlanEntry] = []
    skipped: list[SkippedTexture] = []
    for descriptor, name in zip(descriptors, names):
        index = descriptor.get("TextureIndex", "?")
        png_path = resolve_texture_path(sluggie_path, name)

        expected_width = descriptor.get("Width")
        expected_height = descriptor.get("Height")
        if expected_width is not None and expected_height is not None:
            # Missing files and bad dimensions stay fatal even for mipmapped
            # textures: the skip exception only covers a *validated*
            # unsupported mip layout (PLAN "Required behavior").
            check_png_dimensions(png_path, expected_width, expected_height)

        additional_mip_count = descriptor.get("AdditionalMipCount") or 0
        if additional_mip_count > 0:
            expected_length = descriptor.get("ImagePayloadLength")
            if expected_length is None:
                expected_length = _mip_chain_payload_length(
                    descriptor.get("Format", 0),
                    descriptor.get("Width", 0),
                    descriptor.get("Height", 0),
                    additional_mip_count,
                )
            reason = (
                f"unsupported mip layout: {additional_mip_count} additional "
                "mip level(s) cannot be validated in the single-image "
                "encoding path"
            )
            skipped.append(
                SkippedTexture(
                    texture_index=descriptor.get("TextureIndex", 0),
                    texture_file_name=name,
                    expected_payload_length=expected_length,
                    reason=reason,
                )
            )
            warn(
                f"texture {index} ({name}): {reason}; expected "
                f"{expected_length} bytes; donor image and palette left unchanged"
            )
            continue

        parsed = encoder(
            png_path,
            descriptor.get("Format", 0),
            descriptor.get("PaletteFormat"),
            wimgt_executable=wimgt_executable,
            expected_width=expected_width,
            expected_height=expected_height,
        )

        _validate_parsed_tpl_against_descriptor(descriptor, parsed)

        entries.append(
            TexturePlanEntry(
                texture_index=descriptor.get("TextureIndex", 0),
                texture_file_name=name,
                width=parsed.width,
                height=parsed.height,
                format=parsed.format,
                format_name=parsed.format_name,
                image_data=parsed.image_data,
                palette_data=parsed.palette_data,
                palette_entries=parsed.palette_entries,
                palette_format=parsed.palette_format,
            )
        )

    return TexturePlan(entries=tuple(entries), skipped=tuple(skipped))


def _hex_offset(value: Any, field: str, index: Any) -> int:
    """Parse a descriptor's hex offset field into a non-negative int.

    ``export.py`` emits offsets as hex strings (e.g. ``"0x1234"``); a plain int
    is also accepted. A missing or invalid value raises ValueError naming the
    texture and the offending field.
    """
    if value is None:
        raise ValueError(f"texture {index} is missing {field}")
    try:
        offset = int(value, 16) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"texture {index} has an invalid {field}: {value!r}") from exc
    if offset < 0:
        raise ValueError(f"texture {index} has a negative {field}: {offset}")
    return offset


def _proven_length(value: Any, fallback: int, kind: str, index: Any) -> int:
    """Return the descriptor's proven payload length, or ``fallback`` if absent.

    The proven length (``ImagePayloadLength`` / ``PaletteDataLength``) is the
    authoritative byte length for the write (PLAN 3.2, second bullet). When the
    descriptor does not carry it, the actual encoded length is used so the
    representation stays self-consistent.
    """
    if value is None:
        return fallback
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"texture {index} has an invalid {kind} payload length: {value!r}") from exc
    if length < 0:
        raise ValueError(f"texture {index} has a negative {kind} payload length: {length}")
    return length


def texture_writes_for(descriptor: Mapping[str, Any], entry: TexturePlanEntry) -> tuple[TextureWrite, ...]:
    """Build the image and optional palette writes for one descriptor/entry pair.

    ``descriptor`` supplies the proven payload offsets and lengths
    (``ImageDataOffset``/``ImagePayloadLength`` and, when a palette is present,
    ``PaletteDataOffset``/``PaletteDataLength``). ``entry`` supplies the encoded
    payload bytes (``image_data`` and, when present, ``palette_data``).

    An image write is always produced. A palette write is produced only when the
    entry carries palette bytes. Each write's ``payload_length`` is the
    descriptor's proven length; the :class:`TextureWrite` invariant then requires
    the encoded bytes to be exactly that long.
    """
    index = descriptor.get("TextureIndex", entry.texture_index)

    writes = [
        TextureWrite(
            kind="image",
            texture_index=index,
            offset=_hex_offset(descriptor.get("ImageDataOffset"), "ImageDataOffset", index),
            payload_length=_proven_length(
                descriptor.get("ImagePayloadLength"), len(entry.image_data), "image", index
            ),
            bytes=entry.image_data,
        )
    ]

    if entry.palette_data:
        writes.append(
            TextureWrite(
                kind="palette",
                texture_index=index,
                offset=_hex_offset(descriptor.get("PaletteDataOffset"), "PaletteDataOffset", index),
                payload_length=_proven_length(
                    descriptor.get("PaletteDataLength"), len(entry.palette_data), "palette", index
                ),
                bytes=entry.palette_data,
            )
        )

    return tuple(writes)


def build_texture_writes(
    descriptors: Sequence[Mapping[str, Any]],
    plan: TexturePlan,
) -> tuple[TextureWrite, ...]:
    """Build the complete texture write list for a plan.

    Each plan entry is matched to its descriptor by ``TextureIndex`` and turned
    into an image write plus (when present) a palette write. Writes are returned
    in plan order. This produces the full write list *without* opening the
    output DAT for writing (PLAN 3.2, last bullet); the patcher applies them in
    its existing write phase.
    """
    by_index: dict[Any, Mapping[str, Any]] = {}
    for descriptor in descriptors:
        index = descriptor.get("TextureIndex")
        if index is not None:
            by_index[index] = descriptor

    writes: list[TextureWrite] = []
    for entry in plan:
        descriptor = by_index.get(entry.texture_index)
        if descriptor is None:
            raise ValueError(f"texture {entry.texture_index} has no matching descriptor")
        writes.extend(texture_writes_for(descriptor, entry))
    return tuple(writes)


def encode_png_to_tpl(
    png_path: str | os.PathLike[str],
    gx_format: int,
    palette_format: int | None = None,
    wimgt_executable: str = "wimgt",
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> ParsedSingleImageTpl:
    """Encode a PNG into a single-image TPL using WIMGT with an explicit target.

    The PNG is copied into a temporary directory, WIMGT is invoked with an
    explicit format target, and the resulting single-image TPL is parsed and
    validated. The temporary directory is removed on both success and failure.

    When ``expected_width`` and ``expected_height`` are both provided, the PNG's
    dimensions are checked against them with Pillow *before* WIMGT is invoked
    (PLAN 3.1, fourth bullet); a mismatch raises ValueError without running
    WIMGT.

    This is the base-image (single-level) encoding path. Mipmapped textures
    produce a multi-image TPL and require a dedicated multi-image parser, which
    is a separate work item.

    Raises TextureEncodingError if WIMGT is unavailable, fails, or produces no
    TPL, ValueError for unsupported format/palette combinations, and ValueError
    for a PNG dimension mismatch.
    """
    target = wimgt_target_for(gx_format, palette_format)

    if expected_width is not None and expected_height is not None:
        check_png_dimensions(png_path, expected_width, expected_height)

    from PIL import Image

    with Image.open(png_path) as img:
        base_image = img.copy()

    with tempfile.TemporaryDirectory(prefix="wimgt_encode_") as temp_dir:
        base_name = "tex"
        base_png = os.path.join(temp_dir, f"{base_name}.png")
        base_image.save(base_png, "PNG")

        tpl_path = os.path.join(temp_dir, f"{base_name}.tpl")
        cmd = [
            wimgt_executable, "encode", "-q", "-o",
            "-x", target,
            "-d", tpl_path,
            base_png,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError as exc:
            raise TextureEncodingError(
                f"wimgt executable not found: {wimgt_executable!r}",
                target=target, stdout="", stderr=str(exc), exit_code=None,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TextureEncodingError(
                f"wimgt timed out encoding {png_path}",
                target=target,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                exit_code=None,
            ) from exc

        if result.returncode != 0:
            raise TextureEncodingError(
                f"wimgt failed to encode {png_path} (exit {result.returncode})",
                target=target,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )

        if not os.path.exists(tpl_path):
            raise TextureEncodingError(
                f"wimgt completed without creating {tpl_path}",
                target=target,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )

        with open(tpl_path, "rb") as tpl_file:
            tpl_bytes = tpl_file.read()

    return parse_single_image_tpl(tpl_bytes)


__all__ = [
    "ParsedSingleImageTpl",
    "TPL_MAGIC",
    "WIMGT_IMAGE_TARGETS",
    "WIMGT_PALETTE_TOKENS",
    "TextureEncodingError",
    "TexturePlan",
    "TexturePlanEntry",
    "SkippedTexture",
    "TextureWrite",
    "build_texture_plan",
    "build_texture_writes",
    "texture_writes_for",
    "check_png_dimensions",
    "encode_png_to_tpl",
    "parse_single_image_tpl",
    "parse_single_image_tpl_file",
    "parse_tpl",
    "read_png_dimensions",
    "resolve_tex_dir",
    "resolve_texture_path",
    "validate_texture_descriptors",
    "validate_texture_file_name",
    "wimgt_target_for",
]
