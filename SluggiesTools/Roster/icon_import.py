"""Direct portrait replacement (GUI character grid, Phase 8): a user's image file as a 48x51 portrait.

``load_user_image(path)`` reads and checks the file (pure, no GUI):

* the format is decided by the content, not the extension: Pillow must open
  it as one of ``FORMATS`` (PNG, JPEG, BMP, GIF, TGA, WEBP); anything else
  (unknown bytes, a truncated file, or a format Pillow reads but the list
  leaves out, such as TIFF, PSD or ICO) is refused, naming what was found;
* at most ``MAX_SIDE`` x ``MAX_SIDE`` pixels, checked from the header before
  anything is decoded (which also keeps Pillow's decompression-bomb path out
  of reach); a source smaller than 48x51 warns (upscaled, blurry);
* an animated GIF / WEBP gives its first frame, with a note;
* every colour mode ends as RGBA (palette transparency kept, CMYK converted,
  16-bit greyscale reduced to 8 bits, JPEG EXIF rotation applied).

``fit_user_image(image, fit, trim)`` makes the 48x51 portrait: ``trim`` crops
to the bounding box of alpha >= 128 first (not for ``strict``), then
``icon_art.fit_image`` (``contain``, ``cover`` or ``strict``) and its alpha
hardening; a warning when the art has much partial transparency (soft edges,
shadows), which CMPR's 1-bit alpha rounds to on/off.
"""

import os
import warnings
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

try:
    from . import icon_art
except ImportError:
    import icon_art

FORMATS = ('PNG', 'JPEG', 'BMP', 'GIF', 'TGA', 'WEBP')
EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tga', '.webp')     # the file dialogs' filter only
MAX_SIDE = 4096
PARTIAL_ALPHA_SHARE = 0.03          # partial alpha on more of the art than this warns
DEFAULT_TRIM = True


class IconImportError(ValueError):
    pass


@dataclass
class UserImage:
    image: object                   # RGBA, the source's own size
    format: str
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


def _to_rgba(img):
    if img.mode in ('I', 'I;16', 'I;16B', 'I;16L', 'I;16N'):
        values = np.asarray(img, dtype=np.int64)
        values = values >> 8 if values.max(initial=0) > 255 else values
        return Image.fromarray(np.clip(values, 0, 255).astype(np.uint8), 'L').convert('RGBA')
    return img.convert('RGBA')


def load_user_image(path: str) -> UserImage:
    """The image at ``path`` as RGBA, checked (module docstring); refusals raise ``IconImportError``."""
    name = os.path.basename(path)
    if not os.path.isfile(path):
        raise IconImportError(f'{name}: no such file')
    if os.path.getsize(path) == 0:
        raise IconImportError(f'{name} is empty')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as img:
                fmt = img.format or 'unknown'
                if fmt not in FORMATS:
                    raise IconImportError(f'{name} is a {fmt} image; only {", ".join(FORMATS)} are supported')
                width, height = img.size
                if width < 1 or height < 1:
                    raise IconImportError(f'{name} has no pixels ({width}x{height})')
                if width > MAX_SIDE or height > MAX_SIDE:
                    raise IconImportError(f'{name} is {width}x{height}; at most {MAX_SIDE}x{MAX_SIDE} is supported')
                notes, warns = [], []
                frames = getattr(img, 'n_frames', 1)
                if frames > 1:
                    img.seek(0)
                    notes.append(f'{name} is animated ({frames} frames): its first frame is used')
                img.load()
                if fmt == 'JPEG':
                    img = ImageOps.exif_transpose(img)
                image = _to_rgba(img)
    except IconImportError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise IconImportError(f'{name} is too large to read safely') from exc
    except (OSError, ValueError, SyntaxError, EOFError) as exc:
        detail = str(exc) or type(exc).__name__
        raise IconImportError(f'{name} could not be read as an image ({detail})') from exc
    if image.width < icon_art.ICON_WIDTH or image.height < icon_art.ICON_HEIGHT:
        warns.append(f'{name} is {image.width}x{image.height}, smaller than the {icon_art.ICON_WIDTH}x'
                     f'{icon_art.ICON_HEIGHT} portrait: it is upscaled and will look blurry')
    return UserImage(image, fmt, notes, warns)


def trim_box(image) -> tuple[int, int, int, int] | None:
    """The bounding box of the pixels with alpha >= 128, or None when there are none."""
    return image.getchannel('A').point(lambda a: 255 if a >= 128 else 0).getbbox()


def partial_alpha_share(image) -> float:
    """The share of the image's pixels with partial alpha (1-254)."""
    alpha = np.asarray(image.getchannel('A'))
    return float(np.count_nonzero((alpha > 0) & (alpha < 255))) / max(1, alpha.size)


def fit_user_image(image, fit: str = icon_art.DEFAULT_FIT_MODE, trim: bool = DEFAULT_TRIM):
    """``(48x51 RGBA portrait, warnings)`` from an RGBA ``image`` (module docstring)."""
    icon_art.check_fit_mode(fit)
    warns = []
    if fit == 'strict':
        trim = False
    if trim:
        box = trim_box(image)
        if box is None:
            warns.append('the image is fully transparent: the portrait shows nothing')
        elif box != (0, 0, image.width, image.height):
            image = image.crop(box)
    share = partial_alpha_share(image)
    if share > PARTIAL_ALPHA_SHARE:
        warns.append(f'{share:.0%} of the art is partly transparent (soft edges, shadows): the game\'s portraits '
                     'have on/off transparency only, so it is rounded to on/off')
    try:
        fitted = icon_art.fit_image(image, fit, 'the image')
    except icon_art.IconArtError as exc:
        raise IconImportError(str(exc)) from exc
    return icon_art.harden_alpha(fitted), warns


def prepare(path: str, fit: str = icon_art.DEFAULT_FIT_MODE, trim: bool = DEFAULT_TRIM):
    """``(portrait, notes, warnings)``: ``load_user_image`` + ``fit_user_image``."""
    loaded = load_user_image(path)
    portrait, warns = fit_user_image(loaded.image, fit, trim)
    return portrait, loaded.notes, loaded.warnings + warns
