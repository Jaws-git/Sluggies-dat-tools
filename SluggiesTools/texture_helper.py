from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Any

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
    if image_offset == 0 or image_offset + 12 > len(data):
        raise ValueError("TPL image descriptor is missing or outside the file bounds")

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
        if palette_offset + 12 > len(data):
            raise ValueError("TPL palette descriptor is outside the file bounds")
        palette_entries, _, palette_format, palette_payload_offset = struct.unpack_from(">HHII", data, palette_offset)
        if palette_entries <= 0:
            raise ValueError("palette descriptor has zero entries")

        palette_length = palette_entries * 2
        palette_end = palette_payload_offset + palette_length
        if palette_payload_offset < palette_offset + 12:
            raise ValueError("palette payload overlaps the palette descriptor")
        if palette_payload_offset + palette_length > len(data):
            raise ValueError("palette payload exceeds the file bounds")
        if image_end > palette_payload_offset and palette_payload_offset > image_payload_offset:
            raise ValueError("image and palette payload ranges overlap")
        palette_data = data[palette_payload_offset:palette_end]

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


__all__ = [
    "ParsedSingleImageTpl",
    "TPL_MAGIC",
    "parse_single_image_tpl",
    "parse_single_image_tpl_file",
    "parse_tpl",
]
