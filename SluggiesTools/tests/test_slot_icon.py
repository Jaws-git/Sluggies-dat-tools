"""Direct portrait replacement.

* intake (``Roster/icon_import.py``): every allowed format loads, everything
  else is refused with the format or reason named; animated, small, palette
  and 16-bit sources;
* fit: contain / cover / strict, trim, alpha hardening and its warning;
* planner (``slot_plan.plan_icon``): the changed view's entry for a new ID,
  a spare row and a stock ID; the other view kept by name, from the model
  folder, or from what the game shows; Miis refused; a same-pixel edit is
  ``nothing``;
* merging: last edit per view, patch / clear rules;
* round trip on the synthetic roster (``test_roster_derive.Harness``): a
  front-only edit leaves the side view's encoded blocks byte-identical;
* CLI, dispatcher and the GUI side (``gui_grid``) without Dear PyGui.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from SluggiesTools import gui_grid
from SluggiesTools.Roster import derive, icon_art, icon_import, icons, model_icons, slot_cli, slot_plan, state
from SluggiesTools.tests.test_roster_derive import Harness, portrait
from SluggiesTools.tests.test_slot_plan import STATE_FILE, FakeEnv, make_config, make_state

RED = (200, 30, 30, 255)


def sprite(size=(100, 80), box=(30, 20, 50, 60), colour=RED) -> Image.Image:
    """A coloured rectangle on a transparent canvas."""
    img = Image.new('RGBA', size, (0, 0, 0, 0))
    img.paste(Image.new('RGBA', (box[2] - box[0], box[3] - box[1]), colour), box[:2])
    return img


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())

    def path(self, name):
        return os.path.join(self.tmp, name)

    def save(self, img, name, fmt=None, **kw):
        path = self.path(name)
        img.save(path, fmt, **kw)
        return path


# --------------------------------------------------------------------------
# Intake
# --------------------------------------------------------------------------

class IntakeTests(TempDir):
    def test_every_allowed_format_loads(self):
        base = Image.new('RGB', (60, 70), (10, 120, 200))
        for fmt, ext in (('PNG', 'png'), ('JPEG', 'jpg'), ('BMP', 'bmp'), ('GIF', 'gif'), ('TGA', 'tga'),
                         ('WEBP', 'webp')):
            with self.subTest(fmt):
                loaded = icon_import.load_user_image(self.save(base, f'a.{ext}', fmt))
                self.assertEqual(loaded.format, fmt)
                self.assertEqual((loaded.image.mode, loaded.size), ('RGBA', (60, 70)))
                self.assertEqual(loaded.warnings, [])

    def test_content_decides_not_the_extension(self):
        loaded = icon_import.load_user_image(self.save(Image.new('RGB', (60, 60)), 'really_png.jpg', 'PNG'))
        self.assertEqual(loaded.format, 'PNG')

    def test_refusals_name_the_reason(self):
        with open(self.path('random.png'), 'wb') as f:
            f.write(b'this is not an image, just some text that ends up in a .png file\n' * 4)
        open(self.path('empty.png'), 'wb').close()
        good = self.save(Image.new('RGB', (64, 64), 'red'), 'good.png')
        with open(good, 'rb') as f:
            data = f.read()
        with open(self.path('truncated.png'), 'wb') as f:
            f.write(data[:len(data) // 2])
        self.save(Image.new('RGB', (64, 64)), 'a.tiff', 'TIFF')
        self.save(Image.new('RGB', (4097, 8)), 'wide.png')
        cases = {'random.png': 'could not be read', 'empty.png': 'empty', 'truncated.png': 'could not be read',
                 'a.tiff': 'TIFF', 'wide.png': '4097x8', 'missing.png': 'no such file'}
        for name, reason in cases.items():
            with self.subTest(name), self.assertRaisesRegex(icon_import.IconImportError, reason):
                icon_import.load_user_image(self.path(name))

    def test_animated_gif_gives_its_first_frame(self):
        frames = [Image.new('RGB', (60, 60), c) for c in ('red', 'blue', 'green')]
        path = self.path('anim.gif')
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
        loaded = icon_import.load_user_image(path)
        self.assertEqual(loaded.image.getpixel((5, 5))[:3], (255, 0, 0))
        self.assertTrue(any('first frame' in n for n in loaded.notes))

    def test_small_source_warns(self):
        loaded = icon_import.load_user_image(self.save(Image.new('RGB', (20, 20)), 'small.png'))
        self.assertTrue(any('upscaled' in w for w in loaded.warnings))

    def test_palette_transparency_and_16_bit(self):
        pal = Image.new('P', (60, 60), 0)
        pal.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
        pal.paste(1, (0, 0, 30, 60))
        loaded = icon_import.load_user_image(self.save(pal, 'pal.png', transparency=0))
        self.assertEqual(loaded.image.getpixel((40, 5))[3], 0)
        self.assertEqual(loaded.image.getpixel((5, 5)), (0, 255, 0, 255))
        deep = Image.fromarray(np.full((60, 60), 0x8000, dtype=np.uint16))
        loaded = icon_import.load_user_image(self.save(deep, 'deep.png'))
        self.assertEqual(loaded.image.getpixel((0, 0)), (128, 128, 128, 255))


# --------------------------------------------------------------------------
# Fit
# --------------------------------------------------------------------------

class FitTests(unittest.TestCase):
    def test_contain_and_cover_give_48x51(self):
        for size in ((200, 50), (50, 200), (48, 51), (20, 20)):
            for fit in ('contain', 'cover'):
                with self.subTest(size=size, fit=fit):
                    image, _w = icon_import.fit_user_image(Image.new('RGBA', size, RED), fit, trim=False)
                    self.assertEqual(image.size, (48, 51))

    def test_contain_letterboxes_and_cover_crops(self):
        wide = Image.new('RGBA', (200, 50), RED)
        contained, _ = icon_import.fit_user_image(wide, 'contain', trim=False)
        covered, _ = icon_import.fit_user_image(wide, 'cover', trim=False)
        self.assertEqual(contained.getpixel((24, 0))[3], 0)            # transparent bar
        self.assertEqual(covered.getpixel((24, 0)), RED)

    def test_strict(self):
        with self.assertRaisesRegex(icon_import.IconImportError, '64x64'):
            icon_import.fit_user_image(Image.new('RGBA', (64, 64), RED), 'strict')
        exact = Image.new('RGBA', (48, 51), RED)
        exact.putpixel((3, 3), (1, 2, 3, 255))
        image, _ = icon_import.fit_user_image(exact, 'strict', trim=True)  # trim is ignored for strict
        self.assertEqual(image.tobytes(), exact.tobytes())

    def test_trim_equals_the_fit_of_the_unpadded_sprite(self):
        padded = sprite()
        bare = Image.new('RGBA', (20, 40), RED)
        for fit in ('contain', 'cover'):
            self.assertEqual(icon_import.fit_user_image(padded, fit, trim=True)[0].tobytes(),
                             icon_import.fit_user_image(bare, fit, trim=False)[0].tobytes())
        self.assertNotEqual(icon_import.fit_user_image(padded, 'contain', trim=False)[0].tobytes(),
                            icon_import.fit_user_image(bare, 'contain', trim=False)[0].tobytes())

    def test_fully_transparent_warns(self):
        _img, warns = icon_import.fit_user_image(Image.new('RGBA', (60, 60), (0, 0, 0, 0)))
        self.assertTrue(any('fully transparent' in w for w in warns))

    def test_alpha_hardening_and_its_warning(self):
        img = Image.new('RGBA', (48, 51), (100, 100, 100, 200))
        img.putpixel((0, 0), (100, 100, 100, 100))
        image, warns = icon_import.fit_user_image(img, 'contain', trim=False)
        self.assertEqual(set(np.asarray(image)[..., 3].ravel()), {0, 255})
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(image.getpixel((5, 5)), (100, 100, 100, 255))
        self.assertTrue(any('rounded to on/off' in w for w in warns))
        few = Image.new('RGBA', (48, 51), RED)
        few.putpixel((0, 0), (100, 100, 100, 100))                     # 1 pixel of 2448: under the threshold
        self.assertEqual(icon_import.fit_user_image(few, 'contain', trim=False)[1], [])

    def test_load_portrait_keeps_its_behaviour(self):
        """``icon_art.load_portrait`` is built on the same maths: no trim, hardened."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 's.png')
            sprite().save(path)
            for fit in ('contain', 'cover'):
                self.assertEqual(icon_art.load_portrait(path, fit).tobytes(),
                                 icon_import.fit_user_image(sprite(), fit, trim=False)[0].tobytes())
            with self.assertRaises(icon_art.IconArtError):
                icon_art.load_portrait(path, 'strict')


# --------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------

class IconEnv(FakeEnv):
    def __init__(self, same=False, **kw):
        super().__init__(**kw)
        self.same = same
        self.calls = []

    def same_portrait(self, char, view, image):
        return self.same

    def shown_portrait(self, char, view):
        self.calls.append(('shown', char['id'], view))
        return portrait((1, 2, 3, 255))

    def stock_portrait(self, cid, view):
        self.calls.append(('stock', cid, view))
        return portrait((4, 5, 6, 255))


def pick(colour=RED, label='mine.png'):
    return slot_plan.IconPick(portrait(colour), f'/staged/{label}', label)


def plan_icon(cid, view='front', env=None, config=None, **kw):
    return slot_plan.plan_icon(make_state(), config or make_config(), cid, view, pick(), env or IconEnv(),
                               STATE_FILE, **kw)


def entry_of(config, key, cid):
    return next(e for e in config.get(key) or [] if int(e['id'], 16) == cid)


class PlanIconTests(unittest.TestCase):
    def test_new_id_keeps_its_own_other_view_by_name(self):
        plan = plan_icon(0x66)
        self.assertEqual(entry_of(plan.config, 'ids', 0x66)['icon'],
                         {'front': 'pick_66_front.png', 'side': 'side_0_0.png', 'like': '0x04', 'fit': 'contain'})
        self.assertEqual(list(plan.portraits), ['pick_66_front.png'])
        self.assertEqual(plan.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        self.assertEqual(plan.effects['portraits'], {'front': '/staged/mine.png'})
        self.assertEqual(plan.effects['portrait_note'], 'front portrait from mine.png')

    def test_new_id_without_own_portraits_keeps_what_the_game_shows(self):
        env = IconEnv()
        plan = plan_icon(0x68, 'side', env)
        self.assertEqual(entry_of(plan.config, 'ids', 0x68)['icon'],
                         {'side': 'pick_68_side.png', 'front': 'keep_68_front.png', 'fit': 'contain'})
        self.assertEqual(env.calls, [('shown', 0x68, 'front')])
        self.assertEqual(plan.portraits['keep_68_front.png'].getpixel((0, 0)), (1, 2, 3, 255))

    def test_spare_row_goes_to_its_wheels_entry(self):
        plan = plan_icon(0x47)
        self.assertEqual(plan.config['wheels'][0]['icon']['front'], 'pick_47_front.png')
        self.assertEqual(plan.config['wheels'][0]['icon']['side'], 'keep_47_side.png')

    def test_stock_id_keeps_its_stock_art(self):
        env = IconEnv()
        plan = plan_icon(0x0D, env=env)
        self.assertEqual(plan.config['stock_icons'], [{'id': '0x0D', 'icon': {
            'front': 'pick_0D_front.png', 'side': 'keep_0D_side.png', 'fit': 'contain'}}])
        self.assertEqual(env.calls, [('stock', 0x0D, 'side')])

    def test_other_view_from_the_model_folder(self):
        config = make_config()
        config['stock_icons'] = [{'id': '0x0D', 'icon': {'model': '/m/home'}}]
        plan = plan_icon(0x0D, config=config)
        self.assertEqual(plan.portraits['keep_0D_side.png'], '/m/home/icon/SideIcon.png')
        self.assertEqual(plan.config['stock_icons'][0]['icon']['side'], 'keep_0D_side.png')

    def test_mii_is_refused(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'Mii'):
            plan_icon(0x50)

    def test_same_pixels_is_nothing(self):
        plan = plan_icon(0x66, env=IconEnv(same=True))
        self.assertTrue(plan.nothing)
        self.assertIsNone(plan.config)
        self.assertFalse(plan_icon(0x66, env=IconEnv(same=True), compare=False).nothing)

    def test_input_config_is_not_changed(self):
        config = make_config()
        plan_icon(0x66, config=config)
        self.assertEqual(config, make_config())


# --------------------------------------------------------------------------
# Merging and batches
# --------------------------------------------------------------------------

HP = '/m/27 Bowser/114968608_koopa.gpl/114968608_koopa.gpl.sluggie'


def classify(path):
    return slot_plan.Pair(27, HP, None, path, 'koopa')


def load(edit):
    colour = {'a.png': RED, 'b.png': (0, 0, 255, 255)}.get(os.path.basename(edit.file), (0, 255, 0, 255))
    return slot_plan.IconPick(portrait(colour), edit.file, os.path.basename(edit.file))


def icon(cid, view, name='a.png'):
    return {'op': 'icon', 'id': cid, 'view': view, 'file': f'/img/{name}'}


def batch(*items, env=None):
    return slot_plan.plan_batch(make_state(), make_config(), slot_plan.parse_edits(list(items)), env or IconEnv(),
                                STATE_FILE, classify_fn=classify, load_icon_fn=load)


class MergeTests(unittest.TestCase):
    def test_last_icon_edit_per_view_wins(self):
        b = batch(icon('0x66', 'front', 'a.png'), icon('0x66', 'side', 'c.png'), icon('0x66', 'front', 'b.png'))
        self.assertTrue(b.ok, b.refused)
        self.assertEqual([(e.view, e.file) for e, _p in b.plans], [('side', '/img/c.png'), ('front', '/img/b.png')])
        self.assertEqual(entry_of(b.config, 'ids', 0x66)['icon'],
                         {'front': 'pick_66_front.png', 'side': 'pick_66_side.png', 'like': '0x04', 'fit': 'contain'})
        self.assertEqual(b.portraits['pick_66_front.png'].getpixel((0, 0)), (0, 0, 255, 255))
        self.assertEqual(b.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])

    def test_icon_then_patch_drops_the_icon_edit(self):
        b = batch(icon('0x0D', 'front'), {'op': 'patch', 'id': '0x0D', 'file': HP})
        self.assertEqual([e.op for e, _p in b.plans], ['patch'])
        self.assertTrue(any('portrait edit is dropped' in n for n in b.notes))
        self.assertEqual(b.config['stock_icons'], [{'id': '0x0D', 'icon': {'model': '/m/home'}}])

    def test_patch_then_icon_keeps_it(self):
        b = batch({'op': 'patch', 'id': '0x0D', 'file': HP}, icon('0x0D', 'front'))
        self.assertEqual([e.op for e, _p in b.plans], ['patch', 'icon'])
        self.assertEqual(b.config['stock_icons'][0]['icon'],
                         {'front': 'pick_0D_front.png', 'side': 'keep_0D_side.png', 'fit': 'contain'})
        self.assertEqual(b.portraits['keep_0D_side.png'], '/m/home/icon/SideIcon.png')

    def test_a_patch_without_model_portraits_keeps_the_icon_edit_either_way(self):
        for order in ((0, 1), (1, 0)):
            items = [icon('0x0D', 'front'), {'op': 'patch', 'id': '0x0D', 'file': HP}]
            b = batch(*[items[i] for i in order], env=IconEnv(icons_ok=False))
            self.assertEqual(sorted(e.op for e, _p in b.plans), ['icon', 'patch'])
            self.assertEqual(b.config['stock_icons'][0]['icon']['front'], 'pick_0D_front.png')

    def test_clear_drops_earlier_icon_edits_and_keeps_later_ones(self):
        merged, notes, _ = slot_plan.merge_edits(slot_plan.parse_edits([
            icon('0x66', 'front'), {'op': 'clear', 'id': '0x66'}, icon('0x66', 'side')]), load_icon_fn=load)
        self.assertEqual([(e.op, e.view) for e in merged], [('clear', None), ('icon', 'side')])
        self.assertTrue(any('later clear resets the portraits' in n for n in notes))
        b = batch({'op': 'clear', 'id': '0x66'}, icon('0x66', 'side'), env=IconEnv(same=True))
        self.assertEqual(entry_of(b.config, 'ids', 0x66)['icon']['front'], 'empty_slot_front.png')
        self.assertEqual(entry_of(b.config, 'ids', 0x66)['icon']['side'], 'pick_66_side.png')
        self.assertEqual(b.skipped, [])                         # the clear changed the portraits: no comparison

    def test_a_refused_image_refuses_the_batch(self):
        def refuse(edit):
            raise slot_plan.PlanError('x.png is a TIFF image')
        b = slot_plan.plan_batch(make_state(), make_config(), slot_plan.parse_edits([icon('0x66', 'front')]),
                                 IconEnv(), STATE_FILE, load_icon_fn=refuse)
        self.assertFalse(b.ok)
        self.assertEqual(b.commands, [])
        self.assertIn('TIFF', b.refused[0][1])

    def test_edit_json_and_parsing(self):
        (e,) = slot_plan.parse_edits([dict(icon('0x66', 'front'), origin='/u/me.png', fit='cover', trim=False)])
        self.assertEqual(e.to_json(), {'op': 'icon', 'id': '0x66', 'file': '/img/a.png', 'view': 'front',
                                       'origin': '/u/me.png', 'fit': 'cover', 'trim': False})
        for bad in ({'view': 'back'}, {'fit': 'stretch'}, {'trim': 'yes'}, {'file': None}):
            with self.subTest(bad), self.assertRaises(slot_plan.PlanError):
                slot_plan.parse_edits([dict(icon('0x66', 'front'), **bad)])

    def test_load_icon_reads_and_fits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'big.png')
            sprite().save(path)
            got = slot_plan.load_icon(slot_plan.Edit('icon', 0x66, path, view='front', origin='/u/me.png'))
            self.assertEqual((got.image.size, got.label, got.path), ((48, 51), 'me.png', path))
            with open(path, 'wb') as f:
                f.write(b'nope')
            with self.assertRaises(slot_plan.PlanError):
                slot_plan.load_icon(slot_plan.Edit('icon', 0x66, path, view='front'))


# --------------------------------------------------------------------------
# Round trip on the synthetic roster
# --------------------------------------------------------------------------

class RoundTripTests(Harness):
    def apply(self, config, edits):
        """Build ``config``, plan ``edits`` on its derived config with the real-file env, rebuild; returns
        (state before, state after, DOL image after, DAT after, batch)."""
        _dol, _dat, _ctx, dat, image = self.build(config)
        derived = derive.derive(image, dat)
        st = state.read_state(image, dat)
        b = slot_plan.plan_batch(st, derived.config, slot_plan.parse_edits(edits), slot_cli.FileEnv(image, dat),
                                 STATE_FILE)
        self.assertTrue(b.ok, b.refused)
        portraits = dict(derived.portraits)
        for name, art in b.portraits.items():
            if isinstance(art, str):
                with Image.open(art) as img:
                    art = img.convert('RGBA')
            portraits[name] = (art, None)
        path = derive.write(derive.Derived(b.config, portraits), os.path.join(self.tmp, f'plan{self.runs}'))
        with open(path, encoding='utf-8') as f:
            config2 = json.load(f)
        _dol2, _dat2, _ctx2, dat_b, image_b = self.build(config2, derive.icon_dir_of(path))
        return (image, dat), (image_b, dat_b), b

    @staticmethod
    def cell(files, cid, view):
        """(CMPR blocks, decoded 48x51 pixels) of what ``cid`` shows for ``view``."""
        image, dat = files
        bank = derive.state_icons.read_bank(image, dat)
        st = state.read_state(image, dat)
        ref = next(c for c in st['characters'] if c['id'] == cid)['icon'][view]
        page = bank.page(ref['page'])
        x, y, w, h = ref['rect']
        pixels = bank.decode_page(ref['page'])[y:y + h, x:x + w]
        blocks = icon_art.cell_blocks(page['image'], page['width'], x, y, icons.CELL) if page['format'] == 14 else None
        return blocks, pixels, ref['source']

    def write_image(self, colour, name='new.png'):
        path = os.path.join(self.tmp, name)
        Image.new('RGBA', (96, 102), colour).save(path)
        return path

    def test_front_only_edit_on_a_new_id_keeps_the_side_blocks(self):
        config = {'ids': [{'id': '0x66', 'template': '0x00', 'icon': {'side': 'red.png', 'front': 'blue.png'}}],
                  'stock_icons': [{'id': '0x02', 'icon': {'side': 'grey.png', 'front': 'green.png'}}]}
        before, after, _b = self.apply(config, [
            {'op': 'icon', 'id': '0x66', 'view': 'front', 'file': self.write_image((250, 200, 0, 255))},
            {'op': 'icon', 'id': '0x02', 'view': 'side', 'file': self.write_image((0, 200, 250, 255), 'n2.png')}])
        for cid, kept in ((0x66, 'side'), (0x02, 'front')):
            self.assertEqual(self.cell(after, cid, kept)[0], self.cell(before, cid, kept)[0], (cid, kept))
        np.testing.assert_allclose(self.cell(after, 0x66, 'front')[1][25, 24].astype(int), (250, 200, 0, 255),
                                   atol=12)                     # the new art (through the stand-in encoder)
        self.assertNotEqual(self.cell(after, 0x02, 'side')[0], self.cell(before, 0x02, 'side')[0])

    def test_a_slot_without_own_portraits_gets_its_current_crop(self):
        config = {'ids': [{'id': '0x66', 'template': '0x00', 'wheel': None}],
                  'grid': {'squares': [['0x66']], 'shape': [11, 5]}}
        before, after, b = self.apply(config, [
            {'op': 'icon', 'id': '0x66', 'view': 'front', 'file': self.write_image((250, 200, 0, 255))},
            {'op': 'icon', 'id': '0x05', 'view': 'front', 'file': self.write_image((0, 200, 250, 255), 'n2.png')}])
        for cid, source in ((0x66, 'template'), (0x05, 'own')):
            self.assertEqual(self.cell(before, cid, 'side')[2], source)
            _blocks, pixels, now = self.cell(after, cid, 'side')
            self.assertEqual(now, 'own')
            # the stock page is C8: the kept crop is re-encoded to CMPR (the stand-in encoder averages each block)
            was = self.cell(before, cid, 'side')[1].astype(int)
            self.assertLess(np.abs(pixels.astype(int) - was)[..., :3].mean(), 40)
            np.testing.assert_array_equal(pixels[..., 3] > 0, was[..., 3] >= 128)
        self.assertEqual(b.config['stock_icons'][0]['icon']['side'], 'keep_05_side.png')

    def test_same_pixels_as_shown_is_nothing(self):
        config = {'ids': [{'id': '0x66', 'template': '0x00', 'icon': {'side': 'red.png', 'front': 'blue.png'}}]}
        _dol, _dat, _ctx, dat, image = self.build(config)
        shown = self.cell((image, dat), 0x66, 'front')[1]
        path = os.path.join(self.tmp, 'same.png')
        Image.fromarray(shown, 'RGBA').save(path)
        st = state.read_state(image, dat)
        b = slot_plan.plan_batch(st, derive.derive(image, dat).config, slot_plan.parse_edits([
            {'op': 'icon', 'id': '0x66', 'view': 'front', 'file': path}]), slot_cli.FileEnv(image, dat), STATE_FILE)
        self.assertEqual((len(b.plans), len(b.skipped), b.commands), (0, 1, []))


# --------------------------------------------------------------------------
# CLI and dispatcher
# --------------------------------------------------------------------------

class CliTests(unittest.TestCase):
    def test_icon_is_a_batch_of_one(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(slot_cli, 'run', return_value=slot_plan.Batch(None)) as run:
            self.assertEqual(slot_cli.main(['--icon', '0x0D', 'side', 'x.png', '--fit', 'cover', '--no-trim',
                                            '--output-dir', os.path.join(tmp, '3_Output_Dat')]), 0)
            with mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                slot_cli.main(['--icon', '0x0D', 'back', 'x.png', '--output-dir', os.path.join(tmp, '3_Output_Dat')])
        (edit,) = run.call_args.args[0]
        self.assertEqual((edit.op, edit.cid, edit.view, edit.fit, edit.trim, edit.file),
                         ('icon', 0x0D, 'side', 'cover', False, os.path.abspath('x.png')))

    def test_dispatcher(self):
        import start
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = os.path.join(tmp, 'plan.json')
            with open(plan_file, 'w') as f:
                json.dump({'commands': [['--roster', '--state', 's.json'], ['--roster-state']]}, f)
            with mock.patch.object(start, 'SLOT_PLAN_FILE', plan_file), \
                    mock.patch('start.subprocess.run', side_effect=[mock.Mock(returncode=0)] * 4) as run:
                self.assertTrue(start.run_slot_chain('0x0D', icon=('front', 'p.png'), fit='strict', trim=False,
                                                     dry_run=True))
                self.assertTrue(start.run_slot_chain('0x0D', icon=('side', 'p.png')))
        planners = [c.args[0] for c in run.call_args_list if '--icon' in c.args[0]]
        self.assertEqual(planners[0][-8:], ['--icon', '0x0D', 'front', os.path.abspath('p.png'), '--fit', 'strict',
                                            '--no-trim', '--dry-run'])
        self.assertEqual(planners[1][-4:], ['--icon', '0x0D', 'side', os.path.abspath('p.png')])

    def test_parse_args(self):
        import start
        with mock.patch('sys.argv', ['start.py', '--set-icon', '0x0D', 'front', 'p.png', '--fit', 'cover']):
            args = start.parse_args()
        self.assertEqual((args.set_icon, args.fit, args.no_trim), (['0x0D', 'front', 'p.png'], 'cover', False))
        for argv in (['--set-icon', '0x0D', 'back', 'p.png'], ['--export', '--fit', 'cover']):
            with self.subTest(argv), mock.patch('sys.argv', ['start.py', *argv]), \
                    mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                start.parse_args()

    def test_writing_flags_know_set_icon(self):
        self.assertTrue(gui_grid.chain_writes([('--set-icon', '0x0D', 'front', 'p.png')]))
        self.assertFalse(gui_grid.chain_writes([('--set-icon', '0x0D', 'front', 'p.png', '--dry-run')]))


# --------------------------------------------------------------------------
# GUI side (no Dear PyGui)
# --------------------------------------------------------------------------

GUI_STATE = {'squares': [], 'characters': [{'id': 0x66, 'name': {'en': 'Red'}}, {'id': 0x0D, 'name': {'en': 'Toad'}}]}


class GuiTests(TempDir):
    def test_mii_rule_matches_the_planner(self):
        for cid in range(0x100):
            self.assertEqual(gui_grid.can_replace_portrait(cid), slot_plan.has_portrait_records(cid), hex(cid))
        self.assertIn('Click to replace the front portrait', gui_grid.portrait_tip(0x0D, 'front'))
        self.assertIn('Mii', gui_grid.portrait_tip(0x50, 'side'))

    def test_preview_follows_fit_and_trim(self):
        preview = gui_grid.IconPreview(self.save(sprite(), 'sprite.png'))
        self.assertTrue(preview.ok)
        first = preview.result()[0].tobytes()
        preview.trim = False
        self.assertNotEqual(preview.result()[0].tobytes(), first)
        preview.fit = 'strict'
        self.assertFalse(preview.ok)
        self.assertEqual(preview.lines()[-1][1], gui_grid.ERROR)
        self.assertEqual(preview.lines()[0], ('sprite.png: PNG, 100x80', gui_grid.TEXT))

    def test_refused_file(self):
        self.save(Image.new('RGB', (64, 64)), 'a.tif', 'TIFF')
        preview = gui_grid.IconPreview(self.path('a.tif'))
        self.assertFalse(preview.ok)
        self.assertEqual(preview.lines(), [('Refused: a.tif is a TIFF image; only PNG, JPEG, BMP, GIF, TGA, WEBP are '
                                            'supported', gui_grid.ERROR)])

    def test_save_and_edit(self):
        preview = gui_grid.IconPreview(self.save(sprite(), 'sprite.png'))
        staged = preview.save(self.path('staged'), 0x66, 'front')
        with Image.open(staged) as img:
            self.assertEqual(img.convert('RGBA').tobytes(), preview.result()[0].tobytes())
        edit = preview.edit(0x66, 'front', staged)
        self.assertEqual(edit, {'op': 'icon', 'id': '0x66', 'view': 'front', 'file': staged, 'fit': 'strict',
                                'trim': False, 'origin': self.path('sprite.png')})
        (parsed,) = slot_plan.parse_edits([edit])                     # the planner takes it as it is
        self.assertEqual(slot_plan.load_icon(parsed).image.tobytes(), preview.result()[0].tobytes())
        gui_grid.prune_staged_icons(self.path('staged'), set())
        self.assertEqual(os.listdir(self.path('staged')), [])

    def test_thumbnail(self):
        self.assertEqual(gui_grid.source_thumbnail(Image.new('RGBA', (48, 51))).size, (192, 204))
        self.assertEqual(gui_grid.source_thumbnail(Image.new('RGBA', (1000, 500))).size, (240, 120))

    def test_pending_labels(self):
        pending = gui_grid.PendingEdits()
        edit = {'op': 'icon', 'id': '0x66', 'view': 'front', 'file': self.path('66_front.png'), 'fit': 'strict',
                'trim': False, 'origin': '/u/me.png'}
        Image.new('RGBA', (48, 51)).save(edit['file'])
        section = {'action': 'icon', 'target': '0x66', 'edit': edit, 'notes': ['Red (0x66): front portrait from '
                                                                              'me.png (48x51)'],
                   'warnings': [], 'rebuild': True,
                   'effects': {'portraits': {'front': edit['file']}, 'portrait_note': 'front portrait from me.png'}}
        pending.accept({'merged': [edit], 'edits': [section]})
        self.assertEqual(pending.lines(0x66), ['Pending: front portrait from me.png'])
        self.assertEqual(pending.portrait(0x66, 'front'), edit['file'])
        self.assertIsNone(pending.portrait(0x66, 'side'))
        self.assertEqual(pending.icon_edit(0x66, 'front'), edit)
        self.assertEqual(gui_grid.staged_icon_files(pending), {edit['file']})
        plan = {'action': 'batch', 'refused': [], 'edits': [section], 'skipped': [], 'notes': [], 'warnings': [],
                'rebuild': True}
        summary = gui_grid.summary_dialog(GUI_STATE, plan, 0, '')
        self.assertIn(('Red (0x66): front portrait from me.png', gui_grid.OK), summary.lines)
        dialog = gui_grid.slot_dialog(GUI_STATE, 0x66, False, plan, 0, '', pending, kind='icon', view='front')
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'New front portrait for Red (0x66)?')
        nothing = dict(plan, edits=[], skipped=[dict(section, notes=['nothing to change'])])
        dialog = gui_grid.slot_dialog(GUI_STATE, 0x66, False, nothing, 0, '', pending, kind='icon', view='front')
        self.assertTrue(dialog.can_apply)                      # staging drops the pending one of that view


if __name__ == '__main__':
    unittest.main()
