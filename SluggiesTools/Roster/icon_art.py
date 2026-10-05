"""Icon art for the roster's icon bank: PNG portraits fitted to 48x51, and pages encoded as CMPR with wimgt.

Moved here from the retired icon pipeline (``prepare_icon_artwork``) unchanged in behaviour: the fit modes and the
alpha hardening (alpha >= 128 opaque, else fully transparent, as CMPR's 1-bit alpha needs) are the same.
"""

import os
import struct
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageOps

ICON_WIDTH, ICON_HEIGHT = 48, 51
FIT_MODES = ('contain', 'cover', 'strict')
DEFAULT_FIT_MODE = 'contain'
TPL_MAGIC = 0x0020AF30
CMPR_FORMAT = 0x0E


class IconArtError(RuntimeError):
    pass


def check_fit_mode(fit_mode: str) -> None:
    if fit_mode not in FIT_MODES:
        raise IconArtError(f'unknown icon fit mode {fit_mode!r}; expected one of: {", ".join(FIT_MODES)}')


def fit_image(image, fit_mode: str = DEFAULT_FIT_MODE, where: str = 'the image'):
    """An RGBA ``image`` fitted into 48x51 (``contain``, ``cover`` or ``strict``), alpha not yet hardened."""
    check_fit_mode(fit_mode)
    size = (ICON_WIDTH, ICON_HEIGHT)
    if image.size == size:
        return image
    if fit_mode == 'strict':
        raise IconArtError(f'{where} is {image.width}x{image.height}; expected {ICON_WIDTH}x{ICON_HEIGHT}')
    if fit_mode == 'contain':
        contained = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
        out = Image.new('RGBA', size, (0, 0, 0, 0))
        out.alpha_composite(contained, ((ICON_WIDTH - contained.width) // 2, (ICON_HEIGHT - contained.height) // 2))
        return out
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def harden_alpha(image):
    """``image`` (RGBA) with CMPR's 1-bit alpha: alpha >= 128 opaque, else fully transparent (black)."""
    pixels = np.array(image.convert('RGBA'))
    opaque = pixels[..., 3] >= 128
    pixels[opaque, 3] = 255
    pixels[~opaque] = 0
    return Image.fromarray(pixels, 'RGBA')


def load_portrait(path: str, fit_mode: str = DEFAULT_FIT_MODE):
    """The PNG at ``path`` as a 48x51 RGBA portrait: ``contain`` (letterboxed), ``cover`` (centre-cropped) or
    ``strict`` (must already be 48x51); alpha hardened."""
    check_fit_mode(fit_mode)
    try:
        with Image.open(path) as source:
            image = source.convert('RGBA')
    except OSError as exc:
        raise IconArtError(f'could not read artwork {path}: {exc}') from exc
    return harden_alpha(fit_image(image, fit_mode, path))


def cmpr_payload(tpl: bytes, size: tuple[int, int]) -> bytes:
    """The image data of a single-image, palette-free CMPR TPL of ``size``."""
    if len(tpl) < 0x20:
        raise IconArtError('wimgt TPL output is truncated')
    magic, count, table = struct.unpack_from('>III', tpl, 0)
    if magic != TPL_MAGIC or count != 1:
        raise IconArtError(f'unexpected TPL header: magic=0x{magic:08X}, images={count}')
    header, palette = struct.unpack_from('>II', tpl, table)
    if palette != 0 or header + 12 > len(tpl):
        raise IconArtError('TPL is not a palette-free single image')
    height, width, fmt, offset = struct.unpack_from('>HHII', tpl, header)
    if (width, height, fmt) != (*size, CMPR_FORMAT):
        raise IconArtError(f'unexpected encoded image: {width}x{height}, format=0x{fmt:X}')
    length = width * height // 2
    if offset < header + 12 or offset + length > len(tpl):
        raise IconArtError('TPL CMPR payload is outside the file')
    return tpl[offset:offset + length]


def _block_offset(page_width: int, bx: int, by: int) -> int:
    """Byte offset of 4x4 sub-block (bx, by) in CMPR data: 8x8 tiles row-major, each TL, TR, BL, BR."""
    tile = (by // 2) * (page_width // 8) + bx // 2
    return tile * 32 + ((by % 2) * 2 + bx % 2) * 8


def _cell_offsets(page_width: int, x: int, y: int, size: int) -> list[int]:
    if x % 4 or y % 4 or size % 4:
        raise IconArtError(f'cell {size}x{size} at ({x}, {y}) is not on whole CMPR sub-blocks')
    return [_block_offset(page_width, bx, by) for by in range(y // 4, (y + size) // 4)
            for bx in range(x // 4, (x + size) // 4)]


def cell_blocks(payload: bytes, page_width: int, x: int, y: int, size: int) -> bytes:
    """The CMPR sub-blocks of the ``size`` x ``size`` cell at (x, y), row by row (``cell_payload`` reads them)."""
    return b''.join(payload[o:o + 8] for o in _cell_offsets(page_width, x, y, size))


def put_cell_blocks(payload: bytearray, page_width: int, x: int, y: int, size: int, blocks: bytes) -> None:
    offsets = _cell_offsets(page_width, x, y, size)
    if len(blocks) != 8 * len(offsets):
        raise IconArtError(f'{len(blocks)} bytes of CMPR blocks for a {size}x{size} cell (expected {8 * len(offsets)})')
    for k, o in enumerate(offsets):
        payload[o:o + 8] = blocks[8 * k:8 * k + 8]


def cell_payload(blocks: bytes, size: int) -> bytes:
    """``cell_blocks`` output as the CMPR data of a stand-alone ``size`` x ``size`` texture (for decoding; ``size``
    rounded up to a whole tile, the extra blocks transparent)."""
    padded = -(-size // 8) * 8
    out = bytearray(struct.pack('>HHI', 0, 0, 0xFFFFFFFF) * (padded * padded // 16))
    put_cell_blocks(out, padded, 0, 0, size, blocks)
    return bytes(out)


def encode_cmpr(image) -> bytes:
    """``image`` (RGBA, sides multiples of 8) as GX CMPR image data, encoded by wimgt."""
    with tempfile.TemporaryDirectory(prefix='sluggies_roster_icons_') as work:
        png, tpl = os.path.join(work, 'page.png'), os.path.join(work, 'page.tpl')
        image.save(png, 'PNG')
        try:
            subprocess.run(['wimgt', 'encode', '-q', '-o', '-x', 'TPL.CMPR', '-d', tpl, png],
                           check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise IconArtError('wimgt was not found on PATH') from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or f'exit code {exc.returncode}'
            raise IconArtError(f'wimgt failed to encode an icon page: {detail}') from exc
        with open(tpl, 'rb') as f:
            return cmpr_payload(f.read(), image.size)
