"""Export-time PNG conversion of material images in other formats.

The patcher only reads PNGs, bound by their file names in the model's tex/
folder. A material image in another format (``FORMATS``) is converted on
export and goes into tex/ as ``<stem>.png``, through the same copy plan as a
PNG linked from outside tex/ (ExportSluggies._plan_external_texture_copy).

The format is decided by the file's content, not its extension (TGA, which
has no signature, by its extension and a plausible header). A format Blender
reads but the list leaves out (TIFF, EXR, HDR, PSD, DDS, ...) is refused:
float and layered data need choices the export should not make silently.

Blender decodes the image (it ships no Pillow); the PNG is written here, so
the same source always gives the same bytes. Conversions are cached in the
temp folder by the source's content hash, so repeated exports and Model Check
reuse them, and a tex/ copy made by an earlier export still matches.

No ``bpy`` import at module level, so it is unit-testable outside Blender.
"""

import hashlib
import os
import struct
import tempfile
import zlib


FORMATS = ('JPEG', 'TGA', 'WEBP', 'BMP')
FORMAT_NAMES = 'PNG, JPEG, TGA, WEBP or BMP'
TGA_EXTENSIONS = ('.tga', '.icb', '.vda', '.vst')
# Bumped when the written PNG changes, so old cache entries are not reused.
_CACHE_VERSION = b'1'

# Recognised but refused (content signature -> name).
_OTHER_SIGNATURES = (
    (b'II*\x00', 'TIFF'), (b'MM\x00*', 'TIFF'), (b'GIF8', 'GIF'), (b'8BPS', 'PSD'),
    (b'\x76\x2f\x31\x01', 'EXR'), (b'#?RADIANCE', 'HDR'), (b'#?RGBE', 'HDR'),
    (b'DDS ', 'DDS'), (b'\x00\x00\x00\x0cjP  ', 'JPEG 2000'), (b'\xff\x4f\xff\x51', 'JPEG 2000'),
)

# Where conversions are cached; None is <temp>/SluggiesConvertedTextures.
cache_dir = None
# converted PNG path (normcase) -> the source it was made from, for reports.
_sources = {}


def needs_conversion(path):
    """True when *path* is not named as a PNG: export converts it."""
    return not os.fspath(path).lower().endswith('.png')


def detect_format(path):
    """The image format of the file at *path* by its content ('PNG', 'JPEG',
    'TGA', 'WEBP', 'BMP', or a refused one such as 'TIFF'), or None when
    unknown or unreadable."""
    try:
        with open(path, 'rb') as handle:
            head = handle.read(32)
    except OSError:
        return None
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'PNG'
    if head.startswith(b'\xff\xd8\xff'):
        return 'JPEG'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'WEBP'
    if head[:2] == b'BM':
        return 'BMP'
    for signature, name in _OTHER_SIGNATURES:
        if head.startswith(signature):
            return name
    # TGA has no signature: color-map type 0/1 and a known image type.
    if os.fspath(path).lower().endswith(TGA_EXTENSIONS) and len(head) >= 18 \
            and head[1] in (0, 1) and head[2] in (1, 2, 3, 9, 10, 11):
        return 'TGA'
    return None


def convert_problem(path):
    """Why the file at *path* cannot be converted ('is a TIFF image', ...),
    or None when it can or does not exist (the existence checks tell)."""
    if not os.path.isfile(path):
        return None
    found = detect_format(path)
    if found in FORMATS or found == 'PNG':
        return None
    if found is None:
        return 'is not an image the export can read'
    return f'is a {found} image'


def converted_name(file_name):
    """The tex/ name a converted *file_name* starts from: ``<stem>.png``."""
    return os.path.splitext(file_name)[0] + '.png'


def source_of(png_path):
    """The source a converted PNG was made from in this session, or None."""
    return _sources.get(os.path.normcase(os.path.abspath(png_path)))


def _cache_folder():
    return cache_dir or os.path.join(tempfile.gettempdir(), 'SluggiesConvertedTextures')


def converted_png(source_path, decode=None):
    """The path of a PNG with the pixels of the image at *source_path*, made
    once per source content (module docstring). *decode* (default
    ``decode_rgba``) gives ``(width, height, top-down RGBA bytes)``.
    Raises ValueError when the file cannot be converted."""
    name = os.path.basename(source_path)
    problem = convert_problem(source_path)
    if problem:
        raise ValueError(f'{name} {problem}; textures must be {FORMAT_NAMES}')
    try:
        with open(source_path, 'rb') as handle:
            data = handle.read()
    except OSError as exc:
        raise ValueError(f'{name} could not be read ({exc})') from exc
    folder = _cache_folder()
    target = os.path.join(folder, hashlib.sha1(_CACHE_VERSION + data).hexdigest() + '.png')
    if not os.path.isfile(target):
        width, height, rgba = (decode or decode_rgba)(source_path)
        png = encode_png(width, height, rgba)
        try:
            os.makedirs(folder, exist_ok=True)
            temp = f'{target}.{os.getpid()}.tmp'
            with open(temp, 'wb') as handle:
                handle.write(png)
            os.replace(temp, target)
        except OSError as exc:
            raise ValueError(f'{name} could not be converted to a PNG ({exc})') from exc
    _sources[os.path.normcase(os.path.abspath(target))] = os.path.abspath(source_path)
    return target


def _chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload \
        + struct.pack('>I', zlib.crc32(kind + payload) & 0xFFFFFFFF)


def encode_png(width, height, rgba):
    """An 8-bit PNG of *width* x *height* top-down RGBA bytes: RGB when every
    pixel is opaque, else RGBA (straight alpha). The same input always gives
    the same bytes."""
    rgba = bytes(rgba)
    count = width * height
    if width < 1 or height < 1 or len(rgba) != count * 4:
        raise ValueError(f'{width}x{height} image with {len(rgba)} bytes of RGBA')
    if rgba[3::4] == b'\xff' * count:
        pixels = bytearray(count * 3)
        for channel in range(3):
            pixels[channel::3] = rgba[channel::4]
        channels, color_type = 3, 2
    else:
        pixels, channels, color_type = rgba, 4, 6
    stride = width * channels
    raw = b''.join(b'\x00' + bytes(pixels[y * stride:(y + 1) * stride]) for y in range(height))
    header = struct.pack('>IIBBBBB', width, height, 8, color_type, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header)
            + _chunk(b'IDAT', zlib.compress(raw, 6)) + _chunk(b'IEND', b''))


def decode_rgba(path):
    """``(width, height, top-down RGBA bytes)`` of the image at *path*, read
    by Blender into a temporary image. Byte images keep their stored values
    (no colour management), as the patcher reads a PNG."""
    import bpy
    import numpy as np

    name = os.path.basename(path)
    try:
        image = bpy.data.images.load(path, check_existing=False)
    except RuntimeError as exc:
        raise ValueError(f'{name} could not be read ({exc})') from exc
    try:
        width, height = image.size
        count = width * height * 4
        if not count or len(image.pixels) != count:
            raise ValueError(f'{name} could not be read')
        pixels = np.empty(count, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    # Blender's rows run bottom-up.
    values = np.clip(np.rint(pixels * 255.0), 0, 255).astype(np.uint8)
    return width, height, values.reshape(height, width, 4)[::-1].tobytes()
