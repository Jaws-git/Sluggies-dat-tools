"""``start.py --apply-slots`` / ``--patch-slot`` / ``--clear-slot`` / ``--rename-slot`` / ``--set-voice`` /
``--set-stats`` / ``--set-icon``: plan the batch chain (``slot_plan.py``).

Reads ``3_Output_Dat`` and derives its config **once**, applies every staged
edit to it in order (``slot_plan.plan_batch``) and writes:

* ``3_Output_Dat/_gui/slot/roster.json`` + ``icons/``: the merged derived
  config (only when the batch needs a roster rebuild);
* ``3_Output_Dat/_gui/slot/plan.json``: the batch's commands, per-edit
  sections (notes, warnings, effects) and refused edits; ``start.py`` then
  runs the commands in order.

A ``--patch`` / ``--clear`` / ``--rename`` / ``--voice`` / ``--stats`` is a
batch of one; ``--apply FILE`` reads an edits file (``{"edits": [{"op":
"patch", "id": "0xNN", "file": ...}, {"op": "clear", "id": "0xNN"}, {"op":
"rename", "id": "0xNN", "text": ...}, {"op": "voice", "id": "0xNN", "source":
"0xMM"}, {"op": "stats", "id": "0xNN", "source": null}, {"op": "icon", "id":
"0xNN", "view": "front", "file": ..., "fit": "contain", "trim": true}, ...]}``;
a null ``source`` goes back to the default; an icon edit's image is read and
fitted here, ``icon_import``). ``--dry-run`` leaves out the build
checks of edits marked ``"checked"`` (the GUI's staging check).

Writes nothing to the game files. Exit code 1 when an edit is refused (the
plan then lists the refused edits and holds no commands).
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
_HS_DIR = os.path.join(_TOOLS_DIR, 'Hammerspace')
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from . import derive, icon_art, icons, ids, model_icons, open_slot, slot_plan, slots, state, state_cli, state_icons
except ImportError:
    import derive
    import icon_art
    import icons
    import ids
    import model_icons
    import open_slot
    import slot_plan
    import slots
    import state
    import state_cli
    import state_icons

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

SOURCE = 'roster.slot'
SLOT_DIR = 'slot'
PLAN_FILE = 'plan.json'
EDITS_FILE = 'edits.json'
# A PNG the roster encoded into the bank decodes back close to, not equal to, its pixels (CMPR is lossy):
# the "empty slot" art measured mean 2.0 / max 32 per channel, other portraits mean 88+.
CMPR_MEAN, CMPR_MAX = 8, 64


def slot_dir(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(output_dir, state_cli.GUI_DIR, SLOT_DIR)


def plan_path(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(slot_dir(output_dir), PLAN_FILE)


class FileEnv(slot_plan.Env):
    """``slot_plan.Env`` on the real files: vanilla skeletons from ``1_Input``, the slot's model from the output."""

    def __init__(self, image, dat):
        self.image, self.dat = image, dat
        self._bank, self._stock_bank, self._pages = None, None, {}
        if _HS_DIR not in sys.path:
            sys.path.insert(0, _HS_DIR)

    def shows_portraits(self, char, found, encoded=False):
        """``encoded``: the PNGs went through the roster's CMPR encoder, so near pixels count as the same."""
        icon = char.get('icon') or {}
        try:
            if self._bank is None:
                self._bank = state_icons.read_bank(self.image, self.dat)
            for view, path in (('side', found.side), ('front', found.front)):
                ref = icon.get(view)
                if not ref:
                    return False
                if ref['page'] not in self._pages:
                    self._pages[ref['page']] = self._bank.decode_page(ref['page'])
                x, y, w, h = ref['rect']
                with Image.open(path) as img:
                    art = np.asarray(img.convert('RGBA'))
                shown = self._pages[ref['page']][y:y + h, x:x + w]
                if encoded and art.shape == shown.shape:
                    diff = np.abs(art.astype(np.int16) - shown.astype(np.int16))
                    if diff.mean() > CMPR_MEAN or diff.max() > CMPR_MAX:
                        return False
                elif not np.array_equal(art, shown):
                    return False
        except (OSError, ValueError, state_icons.IconStateError):
            return False
        return True

    def _crop(self, bank, page, rect):
        key = (id(bank), page)
        if key not in self._pages:
            self._pages[key] = bank.decode_page(page)
        x, y, w, h = rect
        return Image.fromarray(np.ascontiguousarray(self._pages[key][y:y + h, x:x + w]), 'RGBA')

    def _output_bank(self):
        if self._bank is None:
            self._bank = state_icons.read_bank(self.image, self.dat)
        return self._bank

    def shown_portrait(self, char, view):
        ref = (char.get('icon') or {}).get(view)
        if not ref:
            return None
        try:
            return self._crop(self._output_bank(), ref['page'], ref['rect'])
        except (OSError, ValueError, state_icons.IconStateError):
            return None

    def same_portrait(self, char, view, image):
        shown = self.shown_portrait(char, view)
        return shown is not None and shown.size == image.size and np.array_equal(np.asarray(shown),
                                                                                  np.asarray(image.convert('RGBA')))

    def stock_portrait(self, cid, view):
        try:
            if self._stock_bank is None:
                self._stock_bank = state_icons.IconBank(self.dat.read(icons.STOCK_BANK_OFFSET,
                                                                      icons.STOCK_BANK_LENGTH))
            hit = self._stock_bank.key_in_force(view, cid)
            if hit is None:
                return None
            page, rect = self._stock_bank.row_rect(hit[1])
            return self._crop(self._stock_bank, page, rect)
        except (OSError, ValueError, state_icons.IconStateError):
            return None

    def skeleton(self, source, target):
        import SlotTarget
        try:
            return SlotTarget._skeleton(source, target)
        except SlotTarget.TargetError as exc:
            raise slot_plan.PlanError(str(exc)) from exc

    def current_high_stem(self, cid):
        import LodPartnerGuard
        try:
            directory = slots.model_dir(self.image, cid)
        except slots.SlotError:
            return None
        block = LodPartnerGuard.read_current_block(directory, slot_plan.HIGH_FILE)
        summary = LodPartnerGuard.act_summary(block) if block else None
        return summary.stem if summary and not summary.is_low_poly else None

    def at_baseline(self, char, config):
        """Both models are the vanilla blocks of the slot's own source (a new ID: its template, with the
        open-slot name, template stats and the "empty slot" portraits; a stock ID: no replaced portraits)."""
        import LodPartnerGuard
        import UntanglePolicy
        cid = char['id']
        try:
            directory = slots.model_dir(self.image, cid)
        except slots.SlotError:
            if cid < ids.FIRST_NEW or char.get('model_dir') is None:
                return False
            directory = char['model_dir']            # no own directory yet: it loads its template's files
        if cid >= ids.FIRST_NEW:
            entry = slot_plan._entry(config, 'ids', cid)
            if (entry is None or 'stats' in entry or char.get('model_source') != char.get('template')
                    or (char.get('name') or {}).get('en') != open_slot.SLOT_NAME['en']):
                return False
            paths = open_slot.slot_icon_paths()
            if not self.shows_portraits(char, model_icons.ModelIcons(None, paths['side'], paths['front']), encoded=True):
                return False
        else:
            if (slot_plan._entry(config, icons.STOCK_KEY, cid) is not None
                    or slot_plan._entry(config, ids.STOCK_STATS_KEY, cid) is not None):
                return False
            if any(UntanglePolicy.is_split(directory, f) for f in (slot_plan.HIGH_FILE, slot_plan.LOW_FILE)):
                return False                         # split copies: a clear also repairs them
        for file_index in (slot_plan.HIGH_FILE, slot_plan.LOW_FILE):
            block = LodPartnerGuard.read_current_block(directory, file_index)
            if block is None or block != LodPartnerGuard._vanilla_block(directory, file_index):
                return False
        return True


def run(edits: list, output_dir: str = state_cli.OUTPUT_DIR, skip_checked: bool = False) -> slot_plan.Batch:
    """Plan the batch ``edits`` (``slot_plan.Edit``s) and write the files the chain reads."""
    image, dat = state_cli._open(output_dir)
    st = state.read_state(image, dat)
    derived = derive.derive(image, dat)
    folder = slot_dir(output_dir)
    state_file = os.path.join(folder, derive.CONFIG_FILE)
    batch = slot_plan.plan_batch(st, derived.config, edits, FileEnv(image, dat), state_file,
                                 state.read_names(image, dat), skip_checked=skip_checked)
    batch.warnings[:0] = derived.warnings
    os.makedirs(folder, exist_ok=True)
    if batch.config is not None:
        portraits = dict(derived.portraits)
        paths = open_slot.slot_icon_paths() if batch.extra_portraits else {}
        for name, view in batch.extra_portraits.items():
            with Image.open(paths[view]) as img:
                portraits[name] = (img.convert('RGBA'), None)
        for name, art in batch.portraits.items():   # icon edits: picked images, kept views
            if isinstance(art, str):
                with Image.open(art) as img:
                    art = img.convert('RGBA')
            portraits[name] = (art, None)
        derive.write(derive.Derived(batch.config, portraits), folder)
    tmp = plan_path(output_dir) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(batch.to_json(), f, ensure_ascii=False, indent=1)
    os.replace(tmp, plan_path(output_dir))
    return batch


def read_edits(path: str) -> list:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise slot_plan.PlanError(f'could not read the edits file {path}: {exc}') from exc
    return slot_plan.parse_edits(data)


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Plan staged slot edits (GUI character grid).')
    parser.add_argument('--patch', nargs=2, metavar=('0xNN', 'FILE'), help='put a .sluggie (and its partner) into a slot')
    parser.add_argument('--clear', metavar='0xNN', help='return a slot to its baseline')
    parser.add_argument('--rename', nargs=2, metavar=('0xNN', 'TEXT'),
                        help='name a slot (all three languages); blank TEXT resets the name')
    parser.add_argument('--voice', nargs=2, metavar=('0xNN', '0xMM'),
                        help="give slot 0xNN's square the voice of 0xMM's family (- or default: its own)")
    parser.add_argument('--stats', nargs=2, metavar=('0xNN', '0xMM'),
                        help="let slot 0xNN play with stock player 0xMM's stats (- or default: its own)")
    parser.add_argument('--icon', nargs=3, metavar=('0xNN', 'VIEW', 'IMAGE'),
                        help="make an image slot 0xNN's front or side portrait (fitted to 48x51)")
    parser.add_argument('--fit', choices=icon_art.FIT_MODES, default=icon_art.DEFAULT_FIT_MODE,
                        help='--icon: contain (fit inside), cover (fill and crop) or strict (exactly 48x51)')
    parser.add_argument('--no-trim', action='store_true', help='--icon: keep the transparent border')
    parser.add_argument('--apply', metavar='FILE', help='an edits file: every staged edit in one chain')
    parser.add_argument('--dry-run', action='store_true', help='leave out the build checks of "checked" edits')
    parser.add_argument('--output-dir', default=state_cli.OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sum(bool(a) for a in (args.patch, args.clear, args.rename, args.voice, args.stats, args.icon,
                             args.apply)) != 1:
        parser.error('give --patch 0xNN FILE, --clear 0xNN, --rename 0xNN TEXT, --voice 0xNN 0xMM, '
                     '--stats 0xNN 0xMM, --icon 0xNN VIEW IMAGE or --apply FILE')
    if args.icon and args.icon[1] not in slot_plan.VIEWS:
        parser.error('--icon VIEW must be front or side')
    if os.path.exists(plan_path(args.output_dir)):
        os.remove(plan_path(args.output_dir))       # a failed planner leaves no stale chain behind
    try:
        if args.patch:
            edits = [slot_plan.Edit('patch', slots.parse_id(args.patch[0]), os.path.abspath(args.patch[1]), index=1)]
        elif args.clear:
            edits = [slot_plan.Edit('clear', slots.parse_id(args.clear), index=1)]
        elif args.rename:
            edits = [slot_plan.Edit('rename', slots.parse_id(args.rename[0]), text=args.rename[1], index=1)]
        elif args.voice or args.stats:
            op, (target, source) = ('voice', args.voice) if args.voice else ('stats', args.stats)
            edits = [slot_plan.Edit(op, slots.parse_id(target), index=1, source=slot_plan.parse_source(source))]
        elif args.icon:
            target, view, image = args.icon
            edits = [slot_plan.Edit('icon', slots.parse_id(target), os.path.abspath(image), index=1, view=view,
                                    fit=args.fit, trim=not args.no_trim)]
        else:
            edits = read_edits(args.apply)
        batch = run(edits, output_dir=args.output_dir, skip_checked=args.dry_run)
    except (RuntimeError, ValueError) as exc:        # PlanError, SlotError, StateError, DeriveError, config errors
        slogger.error(f'refused, nothing written: {exc}', source=SOURCE)
        return 1
    for note in batch.notes:
        slogger.info(note, source=SOURCE)
    for edit, plan in batch.plans + batch.skipped:
        for note in plan.notes:
            slogger.info(f'{_hex(edit.cid)}: {note}', source=SOURCE)
    for warning in batch.warnings:                   # the derive's, then each edit's
        slogger.warning(warning, source=SOURCE)
    if batch.refused:
        for edit, error in batch.refused:
            slogger.error(f'refused, nothing written: {error}' if len(edits) == 1
                          else f'refused, nothing written: edit {edit.index} ({edit.op} {_hex(edit.cid)}): {error}',
                          source=SOURCE)
        return 1
    slogger.info(f'chain: {len(batch.commands)} commands for {len(batch.plans)} edit(s)'
                 + ('' if batch.config is not None else ' (no roster rebuild)'), source=SOURCE)
    for command in batch.commands:
        slogger.info('  start.py ' + ' '.join(_quote(a) for a in command), source=SOURCE)
    return 0


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def _quote(arg: str) -> str:
    return f'"{arg}"' if ' ' in arg else arg


if __name__ == '__main__':
    raise SystemExit(main())
