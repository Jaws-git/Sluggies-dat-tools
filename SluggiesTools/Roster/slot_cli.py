"""``start.py --apply-slots`` / ``--patch-slot`` / ``--clear-slot`` / ``--rename-slot`` / ``--set-voice`` /
``--set-stats`` / ``--set-icon``: plan the batch chain (``slot_plan.py``).

Reads ``3_Output_Dat`` and derives its config **once**, applies every staged
edit to it in order (``slot_plan.plan_batch``) and writes:

* ``_gui/slot/roster.json`` + ``icons/``: the merged derived
  config (only when the batch needs a roster rebuild);
* ``_gui/slot/plan.json``: the batch's commands, per-edit
  sections (notes, warnings, effects) and refused edits; ``start.py`` then
  runs the commands in order.

A ``--patch`` / ``--clear`` / ``--rename`` / ``--voice`` / ``--stats`` is a
batch of one; ``--apply FILE`` reads an edits file (``{"edits": [{"op":
"patch", "id": "0xNN", "file": ...}, {"op": "clear", "id": "0xNN"}, {"op":
"rename", "id": "0xNN", "text": ...}, {"op": "voice", "id": "0xNN", "source":
"0xMM"}, {"op": "stats", "id": "0xNN", "source": null}, {"op": "icon", "id":
"0xNN", "view": "front", "file": ..., "fit": "contain", "trim": true},
{"op": "stat_edits", "file": ...}, {"op": "stat_reset", "id": "0xNN"}, {"op": "copy", "id": "0xNN", "source":
"0xMM"}, ...]}``; a null ``source`` goes back to
the default; an icon edit's image is read and fitted here, ``icon_import``;
a stat edit file is checked against ``main.dol`` here, ``StatEditor/apply``). ``--dry-run`` leaves out the build
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
    from . import derive, gear, icon_art, icons, ids, model_icons, open_slot, slot_plan, slots, state, state_cli, state_icons
except ImportError:
    import derive
    import gear
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
# A PNG the roster encoded into the bank decodes back close to, not equal to, its pixels (CMPR is lossy):
# the "empty slot" art measured mean 2.0 / max 32 per channel, other portraits mean 88+.
CMPR_MEAN, CMPR_MAX = 8, 64
# A stock (C8) portrait re-encoded to CMPR by a paste: mean 4.5-8.5 per channel over the visible pixels (Bowser,
# Red Toad); different portraits measure far above.
CLOSE_MEAN = 16


def slot_dir(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(state_cli.gui_dir(output_dir), SLOT_DIR)


def plan_path(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(slot_dir(output_dir), PLAN_FILE)


class FileEnv(slot_plan.Env):
    """``slot_plan.Env`` on the real files: vanilla skeletons from ``1_Input``, the slot's model from the output."""

    def __init__(self, image, dat, input_dir=None):
        """``input_dir``: where ``1_Input/main.dol`` lies (the stat edits' baseline); None: no stat edit notes."""
        self.image, self.dat, self.input_dir = image, dat, input_dir
        self._bank, self._stock_bank, self._pages = None, None, {}
        self._stat_edits = None
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

    def close_portrait(self, char, view, image):
        shown = self.shown_portrait(char, view)
        if shown is None or shown.size != image.size:
            return False
        a, b = np.asarray(shown).astype(np.int16), np.asarray(image.convert('RGBA')).astype(np.int16)
        mask = a[..., 3] > 127
        if not np.array_equal(mask, b[..., 3] > 127):
            return False
        return not mask.any() or np.abs(a[..., :3] - b[..., :3])[mask].mean() <= CLOSE_MEAN

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

    def stat_edits(self, path):
        from StatEditor import apply as stat_apply, bridge as stat_bridge, cli as stat_cli
        try:
            prepared, names = stat_cli.check(self.image, self.dat, [path], self.input_dir or stat_cli.INPUT_DIR)
        except (stat_apply.EditFileError, stat_bridge.BridgeError) as exc:
            raise slot_plan.PlanError(str(exc)) from exc
        if not prepared.changes:
            return slot_plan.StatCheck(None)
        return slot_plan.StatCheck(stat_apply.summary(prepared), stat_apply.describe(prepared, names),
                                   prepared.warnings)

    def stat_edited(self, cid):
        if self._stat_edits is None:
            from StatEditor import carry
            found = state_cli.detect_stat_edits(self.image, self.input_dir) if self.input_dir else None
            self._stat_edits = carry.by_character(found) if found is not None else {}   # unreadable: no notes
        edits = self._stat_edits.get(cid)
        return edits.text() if edits else None

    def current_block(self, char, role):
        ref = ((char.get('blocks') if role in slot_plan.MODEL_ROLES else char.get('equipment')) or {}).get(role)
        if not ref or self.dat is None:
            return None
        return self.dat.read(ref['offset'], ref['length'])

    def vanilla_block(self, directory, file):
        import LodPartnerGuard
        try:
            return LodPartnerGuard._vanilla_block(directory, file)
        except (OSError, ValueError):
            return None

    def slot_problems(self, directory, high, low):
        import LodPartnerGuard
        import SlotTarget
        vanilla = {f: self.vanilla_block(directory, f) for f in (slot_plan.HIGH_FILE, slot_plan.LOW_FILE)}
        problems = []
        for f, role, block in ((slot_plan.HIGH_FILE, 'High', high), (slot_plan.LOW_FILE, 'Low', low)):
            errors = SlotTarget.block_errors(block, vanilla[f])
            if errors:
                problems.append(f'the {role} block fails validation: ' + '; '.join(errors[:3]))
        if problems:
            return problems, []
        summaries = {f: LodPartnerGuard.act_summary(b) if b else None for f, b in vanilla.items()}
        try:
            return SlotTarget.slot_pair_problems(high, low, summaries)
        except SlotTarget.TargetError as exc:
            return [str(exc)], []

    def equipment_block_problems(self, directory, file, block):
        import SlotTarget
        return SlotTarget.equipment_block_problems(block, directory, file)

    def stat_snapshot(self, cid):
        from StatEditor import bridge as stat_bridge, carry, fields as stat_fields
        try:
            place = carry.placement(self.image)
        except stat_bridge.BridgeError:
            return None
        if place is None or cid not in place.present:
            return None
        values = {}
        for f in stat_fields.CHARACTER_FIELDS:
            raw = self.image.read(place.layouts[f.table].row_address(cid) + f.offset, f.size)
            values.setdefault(f.group, {})[f.name] = raw.hex()
        chemistry = {}
        for other in place.present:
            row = self.image.read(carry.chem_address(cid, other, place.layouts, place.matrix), 1)[0]
            column = self.image.read(carry.chem_address(other, cid, place.layouts, place.matrix), 1)[0]
            chemistry[_hex(other)] = [row, column]
        return {'fields': values, 'chemistry': chemistry}

    def skeleton(self, source, target):
        import SlotTarget
        try:
            return SlotTarget._skeleton(source, target)
        except SlotTarget.TargetError as exc:
            raise slot_plan.PlanError(str(exc)) from exc

    def equipment_problems(self, source, target):
        import SlotTarget
        try:
            src, dst = SlotTarget._vanilla_summary(source, True), SlotTarget._vanilla_summary(target, True)
        except (OSError, ValueError) as exc:
            raise slot_plan.PlanError(f'could not read the vanilla skeletons: {exc}') from exc
        if src is None:
            raise slot_plan.PlanError('could not read the equipment skeleton to compare (no ACT section)')
        if dst is None:
            return []                                # an empty file has no skeleton to match
        return SlotTarget.skeleton_problems(src, dst)[0]

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
        cid = char['id']
        if cid >= ids.FIRST_NEW:
            entry = slot_plan._entry(config, 'ids', cid)
            if (entry is None or 'stats' in entry or char.get('model_source') != char.get('template')
                    or (char.get('name') or {}).get('en') != open_slot.SLOT_NAME['en']):
                return False
            if not self.shows_open_slot_portraits(char):
                return False
        elif (slot_plan._entry(config, icons.STOCK_KEY, cid) is not None
              or slot_plan._entry(config, ids.STOCK_STATS_KEY, cid) is not None):
            return False
        if not self.models_at_baseline(char):
            return False
        return all(e.get('vanilla') is not False for e in (char.get('equipment') or {}).values())

    def shows_open_slot_portraits(self, char):
        paths = open_slot.slot_icon_paths()
        return self.shows_portraits(char, model_icons.ModelIcons(None, paths['side'], paths['front']), encoded=True)

    def models_at_baseline(self, char):
        import LodPartnerGuard
        import UntanglePolicy
        cid = char['id']
        try:
            directory = slots.model_dir(self.image, cid)
        except slots.SlotError:
            if cid < ids.FIRST_NEW or char.get('model_dir') is None:
                return False
            directory = char['model_dir']            # no own directory yet: it loads its template's files
        for file_index in (slot_plan.HIGH_FILE, slot_plan.LOW_FILE):
            if cid < ids.FIRST_NEW and UntanglePolicy.is_split(directory, file_index):
                import UntangledTextures             # split copies: their own untangled baseline (re-tangled: no)
                if not UntangledTextures.split_at_baseline(directory, file_index):
                    return False
                continue
            block = LodPartnerGuard.read_current_block(directory, file_index)
            if block is None or block != LodPartnerGuard._vanilla_block(directory, file_index):
                return False
        return True


def run(edits: list, output_dir: str = state_cli.OUTPUT_DIR, skip_checked: bool = False) -> slot_plan.Batch:
    """Plan the batch ``edits`` (``slot_plan.Edit``s) and write the files the chain reads."""
    image, dat = state_cli._open(output_dir)
    st = state.read_state(image, dat)
    state_cli.add_vanilla_flags(st)                  # the equipment baseline checks need it
    derived = derive.derive(image, dat)
    folder = slot_dir(output_dir)
    state_file = os.path.join(folder, derive.CONFIG_FILE)
    batch = slot_plan.plan_batch(st, derived.config, edits, FileEnv(image, dat, state_cli.input_dir(output_dir)), state_file,
                                 state.read_names(image, dat), skip_checked=skip_checked)
    batch.warnings[:0] = derived.warnings
    os.makedirs(folder, exist_ok=True)
    write_copy_files(batch.copy_files, slot_plan.copy_dir(state_file))
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


def write_copy_files(files: dict, folder: str) -> None:
    """The pastes' snapshot files (``slot_plan.plan_copy``): blocks (bytes), portraits (RGBA images) and stat values
    (JSON data). The folder is emptied first, so no snapshot of an earlier batch is left for the chain to read."""
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                os.remove(path)
    if not files:
        return
    os.makedirs(folder, exist_ok=True)
    for name, data in files.items():
        path = os.path.join(folder, name)
        if isinstance(data, (bytes, bytearray)):
            with open(path, 'wb') as f:
                f.write(data)
        elif isinstance(data, Image.Image):
            data.save(path, 'PNG')
        else:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)


def _is_equipment(path: str) -> bool:
    """Whether the picked .sluggie is a bat or glove (FileIndex 2-5) rather than a character model."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('SluggiesModel', {}).get('FileIndex') in gear.ROLES
    except (OSError, ValueError):
        return False


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
    parser.add_argument('--copy', nargs=2, metavar=('0xSS', '0xTT'),
                        help='make slot 0xTT a clone of character 0xSS as the game holds it (the grid\'s paste)')
    parser.add_argument('--icon', nargs=3, metavar=('0xNN', 'VIEW', 'IMAGE'),
                        help="make an image slot 0xNN's front or side portrait (fitted to 48x51)")
    parser.add_argument('--fit', choices=icon_art.FIT_MODES, default=icon_art.DEFAULT_FIT_MODE,
                        help='--icon: contain (fit inside), cover (fill and crop) or strict (exactly 48x51)')
    parser.add_argument('--no-trim', action='store_true', help='--icon: keep the transparent border')
    parser.add_argument('--equipment', metavar='FILE', help='with --patch: the slot file (2 bat, 3 left glove, 4 right '
                        'glove, 5 extra bat) an equipment .sluggie goes to (default: the file it was exported from); '
                        'with --clear: reset only that file (or all)')
    parser.add_argument('--no-gear', action='store_true', help='--patch of a model into a new ID: do not take '
                        "the model's bats and gloves along")
    parser.add_argument('--apply', metavar='FILE', help='an edits file: every staged edit in one chain')
    parser.add_argument('--dry-run', action='store_true', help='leave out the build checks of "checked" edits')
    parser.add_argument('--output-dir', default=state_cli.OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sum(bool(a) for a in (args.patch, args.clear, args.rename, args.voice, args.stats, args.icon, args.copy,
                             args.apply)) != 1:
        parser.error('give --patch 0xNN FILE, --clear 0xNN, --rename 0xNN TEXT, --voice 0xNN 0xMM, '
                     '--stats 0xNN 0xMM, --icon 0xNN VIEW IMAGE, --copy 0xSS 0xTT or --apply FILE')
    if args.equipment and not (args.patch or args.clear):
        parser.error('--equipment goes with --patch or --clear')
    if args.no_gear and not args.patch:
        parser.error('--no-gear goes with --patch')
    if args.icon and args.icon[1] not in slot_plan.VIEWS:
        parser.error('--icon VIEW must be front or side')
    if os.path.exists(plan_path(args.output_dir)):
        os.remove(plan_path(args.output_dir))       # a failed planner leaves no stale chain behind
    try:
        if args.patch:
            path = os.path.abspath(args.patch[1])
            cid = slots.parse_id(args.patch[0])
            if _is_equipment(path):
                edits = [slot_plan.Edit('equip', cid, path, index=1, origin='user',
                                        gear_file=None if args.equipment is None else gear.parse_file(args.equipment))]
            else:
                edits = [slot_plan.Edit('patch', cid, path, index=1, no_gear=args.no_gear)]
        elif args.clear and args.equipment:
            cid = slots.parse_id(args.clear)
            files = gear.FILES if args.equipment.lower() == 'all' else (gear.parse_file(args.equipment),)
            edits = [slot_plan.Edit('equip_clear', cid, index=n, gear_file=f) for n, f in enumerate(files, 1)]
        elif args.clear:
            edits = [slot_plan.Edit('clear', slots.parse_id(args.clear), index=1)]
        elif args.rename:
            edits = [slot_plan.Edit('rename', slots.parse_id(args.rename[0]), text=args.rename[1], index=1)]
        elif args.voice or args.stats:
            op, (target, source) = ('voice', args.voice) if args.voice else ('stats', args.stats)
            edits = [slot_plan.Edit(op, slots.parse_id(target), index=1, source=slot_plan.parse_source(source))]
        elif args.copy:
            edits = [slot_plan.Edit(slot_plan.COPY, slots.parse_id(args.copy[1]), index=1,
                                    source=slots.parse_id(args.copy[0]))]
        elif args.icon:
            target, view, image = args.icon
            edits = [slot_plan.Edit('icon', slots.parse_id(target), os.path.abspath(image), index=1, view=view,
                                    fit=args.fit, trim=not args.no_trim)]
        else:
            edits = read_edits(args.apply)
        batch = run(edits, output_dir=args.output_dir, skip_checked=args.dry_run)
    except (RuntimeError, ValueError, gear.GearError) as exc:        # PlanError, SlotError, StateError, DeriveError, config errors
        slogger.error(f'refused, nothing written: {exc}', source=SOURCE)
        return 1
    for note in batch.notes:
        slogger.info(note, source=SOURCE)
    for edit, plan in batch.plans + batch.skipped:
        who = 'stat edits' if edit.op == slot_plan.STAT_EDITS else _hex(edit.cid)
        for note in plan.notes:
            slogger.info(f'{who}: {note}', source=SOURCE)
    for warning in batch.warnings:                   # the derive's, then each edit's
        slogger.warning(warning, source=SOURCE)
    if batch.refused:
        for edit, error in batch.refused:
            slogger.error(f'refused, nothing written: {error}' if len(edits) == 1
                          else f'refused, nothing written: edit {edit.index} ({edit.op}'
                          + ('' if edit.op == slot_plan.STAT_EDITS else f' {_hex(edit.cid)}') + f'): {error}',
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
