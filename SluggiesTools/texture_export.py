"""Texture side of the model export: the PNGs of one model, ``--untangle`` renaming, and the report.

Everything the export does besides reading is here. ``export_textures(model, ...)`` decodes a parsed
model's textures (one wimgt process each, run side by side) into its ``tex`` folder; ``Model0.export_textures``
calls it. With an ``UntangleContext`` a texture whose Dolphin name is taken gets its image bytes changed, in
the PNG and in the output DAT. ``write_texture_hash_overlaps_report`` writes ``texture_hash_overlaps.txt``
after the run.

Imports ``tpl`` only: ``model0.py`` and ``export.py`` import this module, never the other way round.
"""
import concurrent.futures
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field

from tpl import decode_tpl

# wimgt processes decoding one model's textures at a time.
TEXTURE_DECODE_WORKERS = min(8, os.cpu_count() or 2)
# SHA-1 of a TPL this run decoded -> the PNG it wrote into the export folder. The same block is exported under
# several directories and many textures repeat: a TPL with the same bytes is copied, not decoded again.
_DECODED_PNGS = {}

UNTANGLE_SKIP_STADIUMS = True
UNTANGLE_IGNORE_RAW = { 'tex1_64x64_d6da4880cee95b7b_14' , "tex1_64x64_a09662ae19841ea4_14" , "tex1_64x64_8a05f75d65053b44_14", "tex1_64x64_cc3d32b121549b17_14", "tex1_256x1024_bbd4db6290a9ce03_14", "tex1_256x512_0cd3d3567193d20d_14", "tex1_512x512_82ae50a699739e73_14", "tex1_64x64_5f51e327c244f28a_14" }

# Entries may be written with or without the .png extension; compare on basenames only.
UNTANGLE_IGNORE_BASENAME = {
    name[:-4] if name.lower().endswith('.png') else name
    for name in UNTANGLE_IGNORE_RAW
}


@dataclass
class UntangleContext:
    """What one ``--untangle`` export knows across its models. ``None`` in its place means no untangling
    (an export without the flag, and every stadium directory)."""
    #: Dolphin names taken so far, over the whole export.
    seen_names: set = field(default_factory=set)
    #: ``(image address, palette address or None)`` -> ``(name, image override, tlut override)`` of every
    #: texture handled: a block reached from several directory records is changed once (``untangle_texture``).
    handled_images: dict = field(default_factory=dict)
    #: Lines for the "Untangle updates" and "Untangle warnings" parts of the report.
    report_lines: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    #: model address -> ``{texture index: final name}``, read by the ``.sluggie`` document builder.
    name_overrides: dict = field(default_factory=dict)
    max_attempts: int = 8192
    #: The handle of the output DAT the export reads through (``export.open_export_dat`` sets it); changed
    #: image bytes are written there. ``None``: nothing is written.
    dat_output_handle: object = None


def copy_texture_pngs(output_dir, temp_dir):
    # Overwrites the exported PNGs only: edited textures saved under new names
    # stay in tex/.
    tex_dir = os.path.join(output_dir, 'tex')
    os.makedirs(tex_dir, exist_ok=True)
    for filename in os.listdir(temp_dir):
        shutil.copyfile(
            os.path.join(temp_dir, filename),
            os.path.join(tex_dir, filename),
        )


def untangle_texture(untangle_context, tex, dolphin_name, image_abs, model_absolute, tex_ind):
    """``(dolphin name, image override, tlut override)`` of one texture in an ``--untangle`` export: a name
    another texture already took gets its image bytes changed until it hashes to a free one (written to the
    output DAT). A texture is handled once per address (image and palette start): once changed, its bytes hash
    to the new name, so a lookup by name would change it again on every further visit of a block reached from
    several directory records."""
    # //quick hack to prevent destruction of this file that would get "untangled" 395 times in total, destroying much of the picture
    if dolphin_name in UNTANGLE_IGNORE_BASENAME:
        return dolphin_name, None, None
    seen_names = untangle_context.seen_names
    handled = untangle_context.handled_images
    palette_abs = tex.parent.absolute + tex.paletteDataPtr if tex.paletteDataPtr > 0 else None
    address = (image_abs, palette_abs)
    if address in handled:
        dolphin_name, image_override, tlut_override = handled[address]
        seen_names.add(dolphin_name)
        untangle_context.report_lines.append(
            f'Texture file untangle skipped (known address): {dolphin_name}.png '
            f'(model 0x{model_absolute:x}, tex {tex_ind}, image_start 0x{image_abs:x})'
        )
        return dolphin_name, image_override, tlut_override

    image_override = tlut_override = None
    untangled = tex.ensureUniqueDolphinBasename(seen_names, max_attempts=untangle_context.max_attempts)
    dolphin_name = untangled['basename']
    if untangled['changed']:
        image_override = untangled['image_data']
        tlut_override = untangled['tlut_data']
        dat_out = untangle_context.dat_output_handle
        if dat_out:
            dat_out.seek(image_abs)
            dat_out.write(image_override)
        untangle_context.report_lines.append(
            f'Texture file untangled: {untangled["original_basename"]}.png -> {dolphin_name}.png '
            f'(model 0x{model_absolute:x}, tex {tex_ind}, image_start 0x{image_abs:x}, attempts {untangled["attempts"]})'
        )
    elif untangled['warning']:
        untangle_context.warnings.append(
            f'Warning: {untangled["warning"]} (model 0x{model_absolute:x}, tex {tex_ind}, '
            f'image_start 0x{image_abs:x}, basename {untangled["original_basename"]}.png)'
        )
    handled[address] = (dolphin_name, image_override, tlut_override)
    return dolphin_name, image_override, tlut_override


def export_textures(model, untangle_context=None, texture_output_dir=None):
    """Decode the textures of *model* (a parsed ``Model0``; one wimgt process each) and copy the PNGs into
    ``texture_output_dir/tex``; ``{texture index: PNG name}``, empty for a model without a TEX section.

    With an ``UntangleContext`` (``untangle_texture``) a texture whose name is taken gets its image bytes
    changed, in the PNG and, through the context's ``dat_output_handle``, in the output DAT; the final names
    go into the context's ``name_overrides`` for the ``.sluggie`` document."""
    if not model.TEXPalette:
        return {}
    # A folder of this model's own, in the system temp folder: the one shared tex_temp in
    # 2_Output_Assets was emptied per model, so two exports at once took each other's PNGs.
    temp_dir = tempfile.mkdtemp(prefix='sluggies_tex_')
    try:
        decoded = {}
        texture_paths = decode_textures(model, temp_dir, untangle_context, decoded)
        if texture_output_dir:
            copy_texture_pngs(texture_output_dir, temp_dir)
            for digest, png_name in decoded.items():
                _DECODED_PNGS.setdefault(digest, os.path.join(texture_output_dir, 'tex', png_name))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return texture_paths


def decode_textures(model, temp_dir, untangle_context, decoded=None):
    """Decode every texture of *model* into *temp_dir*; ``{texture index: PNG name}``. A TPL whose bytes
    this run decoded before (``_DECODED_PNGS``) is copied from that PNG. ``decoded`` collects ``{TPL SHA-1:
    PNG name}`` of the ones wimgt decoded here."""
    texture_paths = {}
    queued = {}                                      # dolphin name -> its TPL file, in first-seen order
    for tex_ind, tex in enumerate(model.TEXPalette.descriptors):
        image_override = None
        tlut_override = None
        dolphin_name = tex.dolphinTextureBasename()
        image_abs = tex.parent.absolute + tex.dataPtr

        if untangle_context is not None:
            dolphin_name, image_override, tlut_override = untangle_texture(
                untangle_context, tex, dolphin_name, image_abs, model.absolute, tex_ind)
            model_overrides = untangle_context.name_overrides.setdefault(model.absolute, {})
            model_overrides[tex_ind] = dolphin_name

        dolphin_path = os.path.join(temp_dir, dolphin_name)
        if dolphin_name not in queued:               # a name the model has twice is decoded once
            queued[dolphin_name] = tex.writeTpl(
                dolphin_path, image_data_override=image_override, tlut_data_override=tlut_override)
        texture_paths[tex_ind] = dolphin_name + '.png'
    # The TPLs were written one after the other (they read the model's file); the decoding is one wimgt
    # process per texture, run side by side (one at a time was about five minutes of a full export).
    jobs = []
    for name, fname in queued.items():
        png = os.path.join(temp_dir, name + '.png')
        with open(fname, 'rb') as f:
            digest = hashlib.sha1(f.read()).hexdigest()
        known = _DECODED_PNGS.get(digest)
        if known is not None:
            try:
                shutil.copyfile(known, png)
                os.remove(fname)
                continue
            except OSError:
                pass                                 # the earlier PNG is gone: decode again
        jobs.append((fname, png))
        if decoded is not None:
            decoded[digest] = name + '.png'
    with concurrent.futures.ThreadPoolExecutor(max_workers=TEXTURE_DECODE_WORKERS) as pool:
        for future in [pool.submit(decode_tpl, fname, png) for fname, png in jobs]:
            future.result()                          # the first failure fails the model, as before
    return texture_paths


def write_texture_hash_overlaps_report(output_dir, untangle_report_lines=None, untangle_warnings=None):
    """Write repeated PNG filename counts under the export root.

    Any PNG basename encountered more than once anywhere below output_dir is
    reported once in texture_hash_overlaps.txt.
    """
    report_path = os.path.join(output_dir, 'texture_hash_overlaps.txt')
    png_counts = {}

    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if not fname.lower().endswith('.png'):
                continue
            png_counts[fname] = png_counts.get(fname, 0) + 1

    duplicate_names = sorted(
        (name, count) for name, count in png_counts.items() if count > 1
    )

    with open(report_path, 'w', encoding='utf-8') as report_f:
        for name, count in duplicate_names:
            report_f.write(f'{name} (x{count} identical duplicates)\n')
        if untangle_report_lines:
            report_f.write('\n')
            report_f.write('Untangle updates:\n')
            for line in untangle_report_lines:
                report_f.write(line + '\n')
        if untangle_warnings:
            report_f.write('\n')
            report_f.write('Untangle warnings:\n')
            for line in untangle_warnings:
                report_f.write(line + '\n')
