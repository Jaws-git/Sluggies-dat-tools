"""Character grid pictures made with Pillow alone (no Dear PyGui): ``IconPreview`` (the icon dialog's state:
the picked image, fit mode and trim, redrawn in-process with ``Roster/icon_import``), ``source_thumbnail``,
and the drawn tile pictures ``banana_icon`` (item tile) and ``flag_icon`` (stadium tile).

Pillow is imported inside the functions, so importing this module needs none of it. Reached through
``gui_grid``.
"""

import os

if __package__:
    from .grid_common import (
        ERROR, TEXT, WARN, hex_id,
    )
else:
    from grid_common import (
        ERROR, TEXT, WARN, hex_id,
    )


def _icon_import():
    try:
        from Roster import icon_import
    except ImportError:
        from SluggiesTools.Roster import icon_import
    return icon_import


class IconPreview:
    """The icon dialog's state: the picked image (read and checked once, ``Roster/icon_import``), the fit mode and
    the trim switch; ``result()`` redraws the 48x51 portrait in-process (Pillow only)."""

    def __init__(self, path: str):
        self.path = path
        self.fit, self.trim = 'contain', True
        self.source, self.error = None, None
        self._cache = {}
        icon_import = _icon_import()
        try:
            self.source = icon_import.load_user_image(path)
        except icon_import.IconImportError as exc:
            self.error = str(exc)

    def result(self):
        """``(48x51 RGBA portrait or None, warnings, refusal or None)`` for the current fit and trim."""
        if self.source is None:
            return None, [], self.error
        key = (self.fit, self.trim)
        if key not in self._cache:
            icon_import = _icon_import()
            try:
                image, warns = icon_import.fit_user_image(self.source.image, self.fit, self.trim)
                self._cache[key] = (image, self.source.warnings + warns, None)
            except icon_import.IconImportError as exc:
                self._cache[key] = (None, list(self.source.warnings), str(exc))
        return self._cache[key]

    def lines(self) -> list:
        """The dialog's message lines: the source's facts, notes, warnings and a refusal."""
        if self.source is None:
            return [(f'Refused: {self.error}', ERROR)]
        w, h = self.source.size
        lines = [(f'{os.path.basename(self.path)}: {self.source.format}, {w}x{h}', TEXT)]
        lines += [(note, TEXT) for note in self.source.notes]
        _image, warns, error = self.result()
        lines += [(f'Warning: {w_}', WARN) for w_ in warns]
        if error:
            lines.append((f'Refused: {error}', ERROR))
        return lines

    @property
    def ok(self) -> bool:
        return self.result()[0] is not None

    def save(self, folder: str, cid: int, view: str) -> str:
        """Write the portrait into ``folder`` (named by its pixels, so a preview texture never shows a stale file);
        returns its path."""
        import hashlib
        image = self.result()[0]
        digest = hashlib.sha1(image.tobytes()).hexdigest()[:12]
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f'{cid:02X}_{view}_{digest}.png')
        tmp = f'{path}.{os.getpid()}.tmp'       # the staging check reads it next: never a half-written PNG
        try:
            image.save(tmp, 'PNG')
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return path

    def edit(self, cid: int, view: str, staged: str) -> dict:
        """The staged icon edit: the normalised copy, taken as it is (strict, no trim)."""
        return {'op': 'icon', 'id': hex_id(cid), 'view': view, 'file': staged, 'fit': 'strict', 'trim': False,
                'origin': self.path}


SOURCE_BOX = (240, 240)


def source_thumbnail(image, box=SOURCE_BOX):
    """The icon dialog's view of the picked image: scaled to fit ``box`` (up by whole factors, nearest; down
    smoothly)."""
    from PIL import Image
    w, h = image.size
    factor = min(box[0] / w, box[1] / h)
    if factor >= 1:
        whole = int(factor)
        return image.resize((w * whole, h * whole), Image.Resampling.NEAREST) if whole > 1 else image.copy()
    return image.resize((max(1, round(w * factor)), max(1, round(h * factor))), Image.Resampling.LANCZOS)


def banana_icon(size: tuple[int, int]):
    """The item tile's picture: a banana on a transparent background, fitted into ``size`` (an RGBA PIL image)."""
    import math
    from PIL import Image, ImageDraw
    w, h = size
    k = 4                                                    # drawn 4x larger, then scaled down (smooth edges)

    def band(t, offset, scale):
        """A point across the banana at ``t`` (0-1 along it): ``offset`` -1 (inner edge) to 1 (outer edge)."""
        angle = math.radians(190 + 150 * t)                  # a curve hanging down, tips up
        half = 0.5 * (0.10 + 0.90 * math.sin(math.pi * t) ** 0.7) * scale
        radius = 1.0 + offset * half
        return math.cos(angle) * radius, -math.sin(angle) * radius

    steps = [i / 40 for i in range(41)]
    outline = [band(t, 1, 0.62) for t in steps] + [band(t, -1, 0.62) for t in reversed(steps)]
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    fit = min(w * k * 0.84 / (max(xs) - min(xs)), h * k * 0.84 / (max(ys) - min(ys) + 0.25))
    ox = w * k / 2 - fit * (max(xs) + min(xs)) / 2
    oy = h * k / 2 - fit * (max(ys) + min(ys)) / 2 + fit * 0.08

    def px(points):
        return [(ox + x * fit, oy + y * fit) for x, y in points]

    image = Image.new('RGBA', (w * k, h * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    tip = px([band(1, 0, 0.62)])[0]                          # the stem, at the right-hand tip
    draw.line((tip, (tip[0] + 0.06 * fit, tip[1] - 0.22 * fit)), fill=(110, 80, 20, 255), width=int(0.12 * fit))
    draw.polygon(px(outline), fill=(250, 210, 55, 255), outline=(120, 85, 20, 255), width=2 * k)
    middle = steps[6:35]
    draw.polygon(px([band(t, 0.15, 0.62) for t in middle] + [band(t, -0.55, 0.62) for t in reversed(middle)]),
                 fill=(255, 238, 130, 255))                  # highlight along the inner curve
    end = px([band(0, 0, 0.62)])[0]                          # the dark blossom end
    draw.ellipse((end[0] - 0.05 * fit, end[1] - 0.05 * fit, end[0] + 0.05 * fit, end[1] + 0.05 * fit),
                 fill=(90, 60, 15, 255))
    return image.resize((w, h), Image.Resampling.LANCZOS)


def flag_icon(size: tuple[int, int]):
    """The stadium tile's picture: a flag pole with a triangular flag, on a transparent background (an RGBA PIL
    image of ``size``)."""
    from PIL import Image, ImageDraw
    w, h = size
    k = 4                                                    # drawn 4x larger, then scaled down (smooth edges)
    s = min(w, h) * k                                        # a square drawing area, centred
    ox, oy = (w * k - s) / 2, (h * k - s) / 2

    def px(x, y):
        return ox + x * s, oy + y * s

    image = Image.new('RGBA', (w * k, h * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    outline = (60, 40, 30, 255)
    draw.polygon([px(0.37, 0.16), px(0.86, 0.29), px(0.37, 0.42)],      # the flag, right of the pole's top
                 fill=(220, 50, 45, 255), outline=outline, width=2 * k)
    draw.rectangle((*px(0.30, 0.12), *px(0.37, 0.92)), fill=(190, 190, 200, 255), outline=outline, width=k)
    draw.ellipse((*px(0.275, 0.06), *px(0.395, 0.18)), fill=(245, 200, 60, 255), outline=outline, width=k)
    draw.rectangle((*px(0.20, 0.88), *px(0.47, 0.94)), fill=(120, 120, 130, 255), outline=outline, width=k)
    return image.resize((w, h), Image.Resampling.LANCZOS)
