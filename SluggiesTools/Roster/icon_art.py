"""Icon art for the roster's icon bank: PNG portraits fitted to 48x51, and pages encoded as CMPR with wimgt.

Moved here from the retired icon pipeline (``prepare_icon_artwork``) unchanged in behaviour: the fit modes and the
alpha hardening (alpha >= 128 opaque, else fully transparent, as CMPR's 1-bit alpha needs) are the same.
"""

import os
import struct
import subprocess
import tempfile

from PIL import Image, ImageOps

ICON_WIDTH, ICON_HEIGHT = 48, 51
FIT_MODES = ('contain', 'cover', 'strict')
DEFAULT_FIT_MODE = 'contain'
TPL_MAGIC = 0x0020AF30
CMPR_FORMAT = 0x0E


class IconArtError(RuntimeError):
    pass


def load_portrait(path: str, fit_mode: str = DEFAULT_FIT_MODE):
    """The PNG at ``path`` as a 48x51 RGBA portrait: ``contain`` (letterboxed), ``cover`` (centre-cropped) or
    ``strict`` (must already be 48x51); alpha hardened."""
    if fit_mode not in FIT_MODES:
        raise IconArtError(f'unknown icon fit mode {fit_mode!r}; expected one of: {", ".join(FIT_MODES)}')
    size = (ICON_WIDTH, ICON_HEIGHT)
    try:
        with Image.open(path) as source:
            image = source.convert('RGBA')
    except OSError as exc:
        raise IconArtError(f'could not read artwork {path}: {exc}') from exc
    if image.size != size:
        if fit_mode == 'strict':
            raise IconArtError(f'{path} is {image.width}x{image.height}; expected {ICON_WIDTH}x{ICON_HEIGHT}')
        if fit_mode == 'contain':
            contained = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
            image = Image.new('RGBA', size, (0, 0, 0, 0))
            image.alpha_composite(contained, ((ICON_WIDTH - contained.width) // 2,
                                              (ICON_HEIGHT - contained.height) // 2))
        else:
            image = ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    pixels = image.load()
    for y in range(ICON_HEIGHT):
        for x in range(ICON_WIDTH):
            r, g, b, a = pixels[x, y]
            pixels[x, y] = (r, g, b, 255) if a >= 128 else (0, 0, 0, 0)
    return image


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
