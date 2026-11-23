"""Character grid textures: portraits and tile pictures as Dear PyGui textures (scaled by whole factors,
pending and stat borders and the badge baked in with Pillow), their cache, and their release on a re-read.

A mixin of ``gui_character_grid.CharacterGridTab``; the pictures themselves come from ``grid_images`` and
the icon crops ``--roster-state`` wrote.
"""

import os

import dearpygui.dearpygui as dpg
import numpy as np
from PIL import Image

import gui_grid
from gui_grid_layout import (
    BADGE_FONT, BORDER, FONT_REL, FRAME, GRID_ICON, PORTRAIT, TILE_ICONS, _GECKO, _PENDING, _STATS, _WARN,
    _draws, _rgba,
)


class GridTexturesMixin:
    """``CharacterGridTab``'s textures: ``_texture`` / ``_portrait_texture`` (cached per file and look),
    ``_tile_texture``, ``_dialog_texture``, and the retire / drop pair that frees them."""

    def _tile_texture(self, op):
        """The item / stadium tile's texture (``gui_grid.banana_icon`` / ``flag_icon``), made once per read like
        the portraits."""
        name, draw = TILE_ICONS[op]
        key = (name,)
        if key not in self.textures and key in self._retired:
            self.textures[key] = self._retired.pop(key)
        if key not in self.textures:
            try:
                image = draw(GRID_ICON)
            except (OSError, ValueError) as exc:
                self.app.log_line(f'[character grid] the {name} tile picture could not be drawn: {exc}', _WARN)
                return None
            self.textures[key] = dpg.add_static_texture(image.width, image.height, _rgba(image),
                                                        parent='grid_textures')
        return self.textures[key]

    # ------------------------------------------------------------------ portraits
    def _texture(self, path, scale, badge=None, split=None, gecko=False):
        """A static texture of a crop, scaled by a whole factor (nearest-neighbour); None when unreadable.
        ``badge``: a number drawn into the bottom right corner (the grid's slot count). ``split``: the display size
        ``(w, h)`` of a button with both markers (``_split_texture``; ``gecko``: its pending half red)."""
        try:
            stat = os.stat(path)
        except OSError:
            return None
        # the file's time and size are in the key: a crop can change under the same name between two reads
        key = (path, stat.st_mtime_ns, stat.st_size, scale, badge, split, gecko)
        if key not in self.textures and key in self._retired:
            self.textures[key] = self._retired.pop(key)            # unchanged since the last read: kept
        if key not in self.textures:
            try:
                with Image.open(path) as png:
                    image = png.convert('RGBA')
            except OSError:
                return None
            if scale != 1:
                image = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
            if badge is not None:
                self._draw_badge(image, str(badge), scale)
            if split is not None:
                image = self._split_texture(image, split, _GECKO if gecko else _PENDING)
            data = np.asarray(image, dtype=np.float32).ravel() / 255.0
            self.textures[key] = dpg.add_static_texture(image.width, image.height, data, parent='grid_textures')
        return self.textures[key]

    @staticmethod
    def _split_texture(image, size, left_color=_PENDING):
        """``image`` at its display ``size`` inside the image button's frame padding, with the border of a slot that
        has both pending edits and stat edits: the left half orange (``left_color``: red for a Gecko code
        compatibility break), the right half green. Drawn 1:1 with no frame
        padding (``grid_both_theme``); the padding's inner pixels stay transparent, so hover shows as usual."""
        w, h = size
        if image.size != (w, h):
            image = image.resize((w, h), Image.Resampling.BILINEAR)
        out = np.zeros((h + 2 * FRAME, w + 2 * FRAME, 4), dtype=np.uint8)
        out[FRAME:FRAME + h, FRAME:FRAME + w] = np.asarray(image)
        ring = np.ones(out.shape[:2], dtype=bool)
        ring[BORDER:-BORDER, BORDER:-BORDER] = False
        left = np.zeros_like(ring)
        left[:, :out.shape[1] // 2] = True
        out[ring & left] = left_color
        out[ring & ~left] = _STATS
        return Image.fromarray(out, 'RGBA')

    def _draw_badge(self, image, text, scale):
        """A small light number on a dark rounded box in the image's bottom right corner."""
        from PIL import ImageDraw, ImageFont
        font = self._badge_fonts.get(scale)
        if font is None:
            try:
                font = ImageFont.truetype(os.path.join(self.app.root_dir, FONT_REL), BADGE_FONT * scale)
                font.set_variation_by_axes([700, 100])
            except (OSError, ValueError, AttributeError):
                font = ImageFont.load_default()
            self._badge_fonts[scale] = font
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        pad = scale
        w, h = right - left + 4 * pad, bottom - top + 2 * pad
        x0, y0 = image.width - w - pad, image.height - h - pad
        draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=2 * pad, fill=(20, 20, 24, 215))
        draw.text((x0 + 2 * pad - left, y0 + pad - top), text, font=font, fill=(255, 255, 255, 255))

    @_draws
    def _retire_textures(self):
        """Before a redraw after a read: the textures made so far are set aside. The redraw deletes every item
        that showed one and takes back the ones it still shows (same file, time, size and decoration), so an
        unchanged portrait is not opened and uploaded again; ``_drop_retired_textures`` deletes the rest."""
        self._retired.update(self.textures)
        self.textures = {}

    @_draws
    def _drop_retired_textures(self):
        retired, self._retired = self._retired, {}
        for tag in retired.values():
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

    def _portrait_texture(self, cid, view, scale, badge=None, split=None, gecko=False):
        state = self.loader.state
        # _texture stats the file itself (its key) and gives None for a missing one: no second look here
        path = gui_grid.icon_file(state, self.loader.state_path, cid, view, check=False) if state else None
        return self._texture(path, scale, badge, split, gecko) if path else None

    def _dialog_texture(self, path):
        if not path:
            return None
        try:
            with Image.open(path) as png:
                image = png.convert('RGBA').resize(PORTRAIT, Image.Resampling.NEAREST)
        except OSError:
            return None
        return dpg.add_static_texture(image.width, image.height, _rgba(image), parent='grid_dialog_textures')
